"""
PrairieLearn Exam Scheduler
GitHub-integrated infoAssessment.json automation tool.

Workflow:
  1. Authenticate with a GitHub PAT and target repository.
  2. Navigate the courseInstances/ directory tree to select a term and assessment.
  3. Upload a Canvas Roster CSV and map section / email columns.
  4. Assign start and end times to each student section.
  5. Commit the updated infoAssessment.json to a new branch and open a Pull Request.
"""

import datetime
import json
import re
from pathlib import Path

import pandas as pd
import pytz
import streamlit as st
from github import Github, GithubException, UnknownObjectException

from audit_log import audit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSON_FILE_NAME = "infoAssessment.json"

# SDC default parameters (easily editable)
SDC_BASE_EXAM_MIN = 50  # fallback base exam length (minutes)

# Credential persistence — stores repo URL & PAT in a local JSON file
# so users don't need to re-enter them on every visit.
_CREDENTIALS_FILE = Path(__file__).parent / ".pl_credentials.json"


def _load_credentials() -> dict:
    """Read saved credentials from the local file."""
    if _CREDENTIALS_FILE.exists():
        try:
            return json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_credentials(repo_url: str, pat: str) -> None:
    """Persist repo URL and PAT to the local credentials file."""
    _CREDENTIALS_FILE.write_text(
        json.dumps({"repo_url": repo_url, "pat": pat}, indent=2),
        encoding="utf-8",
    )


def _clear_credentials() -> None:
    """Delete the saved credentials file."""
    _CREDENTIALS_FILE.unlink(missing_ok=True)


# Ordered list of (display_label, tz_name) tuples for the timezone selector.
# The first entry is the default (California).
COMMON_TIMEZONES: list[tuple[str, str]] = [
    ("America/Los_Angeles  —  Pacific (PST / PDT)",   "America/Los_Angeles"),
    ("America/Denver       —  Mountain (MST / MDT)",  "America/Denver"),
    ("America/Chicago      —  Central (CST / CDT)",   "America/Chicago"),
    ("America/New_York     —  Eastern (EST / EDT)",   "America/New_York"),
    ("America/Phoenix      —  Arizona (MST, no DST)", "America/Phoenix"),
    ("America/Anchorage    —  Alaska (AKST / AKDT)",  "America/Anchorage"),
    ("Pacific/Honolulu     —  Hawaii (HST)",           "Pacific/Honolulu"),
    ("UTC",                                            "UTC"),
]

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="PrairieLearn Exam Scheduler",
    page_icon=None,
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

_STATE_DEFAULTS: dict = {
    "authenticated": False,
    "repo": None,           # PyGithub Repository object
    "repo_full_name": "",   # used to detect repo changes and invalidate caches
    "terms_cache": [],      # cached list of term folder names
    "assessments_cache": {},  # {term_name: [assessment_names]}
    "_file_reset": 0,        # counter for resetting file uploaders
}

for _k, _v in _STATE_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def parse_repo_slug(url: str) -> str:
    """Extract 'owner/repo' from a GitHub HTTPS or SSH URL."""
    # Accepts: https://github.com/owner/repo  or  git@github.com:owner/repo
    match = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", url.strip())
    if not match:
        raise ValueError(
            f"Could not parse a valid owner/repo slug from: {url!r}\n"
            "Expected format: https://github.com/owner/repo"
        )
    return match.group(1)


def combine_datetime(date: datetime.date, time: datetime.time) -> str:
    """Produce a PrairieLearn-compatible ISO datetime string (naive local time)."""
    return datetime.datetime.combine(date, time).strftime("%Y-%m-%dT%H:%M:%S")


def get_tz_label(tz_name: str, date: datetime.date) -> str:
    """
    Return a human-readable UTC offset label for a given timezone on a
    specific date, e.g. 'PDT (UTC-07:00)' or 'PST (UTC-08:00)'.
    DST is resolved automatically based on the supplied date.
    """
    tz = pytz.timezone(tz_name)
    ref = tz.localize(datetime.datetime.combine(date, datetime.time(12, 0)))
    abbrev = ref.strftime("%Z")
    raw_offset = ref.strftime("%z")          # e.g. -0700
    offset_fmt = f"{raw_offset[:3]}:{raw_offset[3:]}"  # -07:00
    return f"{abbrev} (UTC{offset_fmt})"


def load_csv_dataframe(uploaded_file) -> pd.DataFrame:
    """Parse a Canvas Roster CSV upload and return a DataFrame."""
    try:
        return pd.read_csv(uploaded_file)
    except Exception as exc:
        raise ValueError(f"Cannot read CSV: {exc}") from exc


# ---------------------------------------------------------------------------
# Date / time option helpers for combobox-style selectboxes
# ---------------------------------------------------------------------------

def _next_weekday(start: datetime.date, weekday: int = 0) -> datetime.date:
    """Return the next date with the given weekday (0=Monday)."""
    days_ahead = weekday - start.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return start + datetime.timedelta(days=days_ahead)


def _build_date_options(
    num_days: int = 90,
) -> tuple[list[str], list[datetime.date]]:
    """
    Generate a list of upcoming dates formatted as 'YYYY-MM-DD (Mon)'
    for use in a searchable selectbox.  Returns (labels, date_objects).
    """
    today = datetime.date.today()
    labels: list[str] = []
    dates: list[datetime.date] = []
    for i in range(num_days):
        d = today + datetime.timedelta(days=i)
        labels.append(d.strftime("%Y-%m-%d (%a)"))
        dates.append(d)
    return labels, dates


def _build_time_options(
    step_min: int = 5,
) -> tuple[list[str], list[datetime.time]]:
    """
    Generate a list of times at *step_min*-minute intervals formatted as
    'HH:MM' for use in a searchable selectbox.
    Returns (labels, time_objects).
    """
    labels: list[str] = []
    times: list[datetime.time] = []
    total_slots = (24 * 60) // step_min
    for i in range(total_slots):
        minutes = i * step_min
        t = datetime.time(minutes // 60, minutes % 60)
        labels.append(t.strftime("%H:%M"))
        times.append(t)
    return labels, times


# Pre-compute option lists (shared across all section rows)
_DATE_LABELS, _DATE_VALUES = _build_date_options()
_TIME_LABELS, _TIME_VALUES = _build_time_options()

# Default slot: next Monday 10:00-10:50
# But users can select any date in the 90-day range, including today
_DEFAULT_DATE = _next_weekday(datetime.date.today(), weekday=0)
_DEFAULT_DATE_IDX = (
    _DATE_VALUES.index(_DEFAULT_DATE) if _DEFAULT_DATE in _DATE_VALUES else 0
)
_DEFAULT_START_IDX = _TIME_LABELS.index("10:00")
_DEFAULT_END_IDX = _TIME_LABELS.index("10:50")


def _normalize_name(name: str) -> str:
    """Lowercase, strip, collapse whitespace for fuzzy name matching."""
    return " ".join(str(name).lower().split())


def _first_last(name: str) -> tuple[str, str]:
    """Extract (first, last) from a normalized name string."""
    parts = name.split()
    if len(parts) < 2:
        return (name, "")
    return (parts[0], parts[-1])


def merge_sdc_sections(grouped: dict[str, list[str]]) -> dict[str, list[str]]:
    """
    Merge ", SDC" sub-sections back into their parent section.

    Canvas sometimes splits SDC students into separate sub-sections like
    "ECS 032A B02 SQ 2026, SDC".  This merges them into the base section
    ("ECS 032A B02 SQ 2026") so all students share the same time-slot
    configuration.  SDC extended-time is handled separately via the
    multiplier CSV.
    """
    merged: dict[str, list[str]] = {}
    for section, uids in grouped.items():
        # Detect ", SDC" suffix (case-insensitive)
        if re.search(r",\s*SDC\s*$", section, re.IGNORECASE):
            base = re.sub(r",\s*SDC\s*$", "", section, flags=re.IGNORECASE).strip()
        else:
            base = section
        merged.setdefault(base, []).extend(uids)
    return merged


def match_sdc_to_roster(
    sdc_df: pd.DataFrame,
    roster_df: pd.DataFrame,
    sdc_name_col: str,
    sdc_mult_col: str,
    roster_name_col: str,
    roster_email_col: str,
) -> tuple[list[dict], list[str]]:
    """
    Cross-reference SDC multiplier table with the Canvas roster by student
    name.  Returns (matched, unmatched_names).

    Each entry in *matched* is {"uid": str, "name": str, "multiplier": float}.
    *unmatched_names* lists SDC names that could not be found in the roster.

    Matching strategy (in order):
      1. Exact full-name match (after normalization).
      2. First-name + last-name match, used only when unambiguous
         (exactly one roster entry shares the same first & last name).
    """
    # Build full-name → uid and (first, last) → [uid] lookups from roster
    full_to_uid: dict[str, str] = {}
    fl_to_uids: dict[tuple[str, str], list[str]] = {}

    for _, row in roster_df.iterrows():
        norm = _normalize_name(row[roster_name_col])
        uid = str(row[roster_email_col]).strip()
        if not uid or uid == "nan":
            continue
        full_to_uid[norm] = uid
        fl = _first_last(norm)
        fl_to_uids.setdefault(fl, []).append(uid)

    matched: list[dict] = []
    unmatched: list[str] = []

    for _, row in sdc_df.iterrows():
        raw_name = str(row[sdc_name_col]).strip()
        norm = _normalize_name(raw_name)
        mult = float(row[sdc_mult_col])

        # 1. Try exact full-name match
        uid = full_to_uid.get(norm)

        # 2. Fall back to first+last match if unambiguous
        if uid is None:
            fl = _first_last(norm)
            candidates = fl_to_uids.get(fl, [])
            if len(candidates) == 1:
                uid = candidates[0]

        if uid:
            matched.append({"uid": uid, "name": raw_name, "multiplier": mult})
        else:
            unmatched.append(raw_name)

    return matched, unmatched


def group_sdc_by_multiplier(
    matched: list[dict],
) -> dict[float, list[str]]:
    """
    Group matched SDC students by their multiplier value.
    Returns {multiplier: [uid, ...]}.
    """
    groups: dict[float, list[str]] = {}
    for entry in matched:
        groups.setdefault(entry["multiplier"], []).append(entry["uid"])
    return groups


def build_allow_access(
    sections: list[str],
    grouped: dict[str, list[str]],
    slot_configs: dict[str, dict],
    sdc_groups: list[dict] | None = None,
) -> list[dict]:
    """
    Construct the allowAccess array from the per-section schedule.

    If *sdc_groups* is provided, one block per multiplier group is appended
    with its own computed timeLimitMin and showClosedAssessment=false.

    Each dict in sdc_groups must contain:
        uids, date, start, end, timeLimitMin, multiplier
    """
    entries = []
    for section in sections:
        cfg = slot_configs[section]
        # Compute timeLimitMin from the section's own start/end window
        _start_dt = datetime.datetime.combine(cfg["date"], cfg["start"])
        _end_dt = datetime.datetime.combine(cfg["date"], cfg["end"])
        _section_limit = max(int((_end_dt - _start_dt).total_seconds() / 60), 1)
        entries.append(
            {
                "comment": section,
                "startDate": combine_datetime(cfg["date"], cfg["start"]),
                "endDate": combine_datetime(cfg["date"], cfg["end"]),
                "uids": sorted(grouped[section]),
                "credit": 100,
                "timeLimitMin": _section_limit,
                "showClosedAssessment": False,
                "showClosedAssessmentScore": False,
            }
        )

    # Append dedicated SDC blocks (one per multiplier group)
    if sdc_groups:
        for sg in sdc_groups:
            if not sg.get("uids"):
                continue
            sdc_entry: dict = {
                "comment": f"SDC Accommodations ({sg['multiplier']}x)",
                "startDate": combine_datetime(sg["date"], sg["start"]),
                "endDate": combine_datetime(
                    sg.get("end_date", sg["date"]), sg["end"]
                ),
                "uids": sorted(sg["uids"]),
                "credit": 100,
                "timeLimitMin": sg["timeLimitMin"],
                "showClosedAssessment": False,
                "showClosedAssessmentScore": False,
            }
            entries.append(sdc_entry)

    # Global fallback: hide grades until instructor releases them
    entries.append(
        {
            "comment": "So students can't see their grade until we set a designated time later",
            "active": False,
            "showClosedAssessment": False,
            "showClosedAssessmentScore": False,
        }
    )

    return entries


def build_pr_body(
    assessment_id: str,
    sections: list[str],
    slot_configs: dict[str, dict],
    grouped: dict,
    tz_name: str = "America/Los_Angeles",
    sdc_groups: list[dict] | None = None,
) -> str:
    """Compose the Pull Request description body (Markdown)."""
    rows = []
    for section in sections:
        cfg = slot_configs[section]
        tz_label = get_tz_label(tz_name, cfg["date"])
        rows.append(
            f"| {section} | {len(grouped[section])} "
            f"| {combine_datetime(cfg['date'], cfg['start'])} "
            f"| {combine_datetime(cfg['date'], cfg['end'])} "
            f"| {tz_label} |"
        )

    # SDC rows (one per multiplier group)
    sdc_block_lines: list[str] = []
    if sdc_groups:
        sdc_block_lines.append("")
        sdc_block_lines.append("### SDC / Accommodations")
        sdc_block_lines.append("")
        for sg in sdc_groups:
            if not sg.get("uids"):
                continue
            tz_label = get_tz_label(tz_name, sg["date"])
            mult_label = f"{sg['multiplier']}x"
            rows.append(
                f"| **SDC ({mult_label})** | {len(sg['uids'])} "
                f"| {combine_datetime(sg['date'], sg['start'])} "
                f"| {combine_datetime(sg.get('end_date', sg['date']), sg['end'])} "
                f"| {tz_label} |"
            )
            sdc_block_lines.append(
                f"- **{mult_label} group** ({len(sg['uids'])} students): "
                f"timeLimitMin={sg['timeLimitMin']}, "
                f"showClosedAssessment=false"
            )

    return "\n".join(
        [
            f"## Auto-scheduled access rules for `{assessment_id}`",
            "",
            "This pull request was generated automatically by the "
            "PrairieLearn Exam Scheduler.",
            "",
            f"**Timezone:** {tz_name}",
            "",
            "### Section schedule",
            "",
            "| Section | Students | Start | End | Offset |",
            "|---------|----------|-------|-----|--------|",
        ]
        + rows
        + sdc_block_lines
        + [
            "",
            "Times are written as wall-clock local time (no UTC offset in "
            "the JSON). The offset column is shown for reference only.",
            "",
            "The `allowAccess` array in `infoAssessment.json` has been "
            "completely replaced with the schedule above.",
            "Please review the diff carefully before merging.",
        ]
    )


def disconnect():
    """Reset all GitHub-related session state."""
    for key, default in _STATE_DEFAULTS.items():
        # Reset to original default (make a copy for mutable defaults)
        st.session_state[key] = (
            default.copy() if isinstance(default, (dict, list)) else default
        )


# ---------------------------------------------------------------------------
# SIDEBAR — Step 1: GitHub Authentication
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Step 1 — GitHub Authentication")

    _creds = _load_credentials()

    repo_url_input = st.text_input(
        "Repository URL",
        value=_creds.get("repo_url", ""),
        placeholder="https://github.com/owner/pl-course-repo",
        help="Full URL of the PrairieLearn course repository on GitHub.",
    ) or ""

    pat_input = st.text_input(
        "Personal Access Token (PAT)",
        value=_creds.get("pat", ""),
        type="password",
        help=(
            "A GitHub PAT with Contents (Read & Write) and "
            "Pull Requests (Read & Write) permissions."
        ),
    ) or ""

    connect_clicked = st.button(
        "Connect", type="primary", width='stretch'
    )

    if connect_clicked:
        if not repo_url_input.strip() or not pat_input.strip():
            st.error("Both the Repository URL and a PAT are required.")
        else:
            try:
                slug = parse_repo_slug(repo_url_input)
            except ValueError as exc:
                st.error(str(exc))
            else:
                with st.spinner("Connecting to GitHub..."):
                    try:
                        gh = Github(pat_input.strip())
                        repo_obj = gh.get_repo(slug)
                        # Validate that this looks like a PrairieLearn repo
                        repo_obj.get_contents("courseInstances")

                        st.session_state.authenticated = True
                        st.session_state.repo = repo_obj
                        st.session_state.repo_full_name = repo_obj.full_name
                        st.session_state.terms_cache = []
                        st.session_state.assessments_cache = {}

                        # Persist credentials locally
                        _save_credentials(
                            repo_url_input.strip(), pat_input.strip()
                        )

                        audit(
                            "auth",
                            detail=f"Connected to {repo_obj.full_name}",
                            pat=pat_input.strip(),
                        )

                    except UnknownObjectException:
                        st.error(
                            "Connected to the repository, but the "
                            "`courseInstances/` directory was not found. "
                            "Confirm this is a PrairieLearn repository."
                        )
                    except GithubException as exc:
                        if exc.status == 401:
                            st.error(
                                "Authentication failed. "
                                "Verify the PAT and its expiry date."
                            )
                        elif exc.status == 404:
                            st.error(
                                "Repository not found. "
                                "Check the URL and PAT permissions."
                            )
                        elif exc.status == 403:
                            st.error(
                                "Access denied or API rate limit reached. "
                                f"Details: {exc.data.get('message', '')}"
                            )
                        else:
                            st.error(
                                f"GitHub API error ({exc.status}): "
                                f"{exc.data.get('message', str(exc))}"
                            )

    # Persistent connection status
    if st.session_state.authenticated:
        repo = st.session_state.repo
        st.success(f"Connected: **{repo.full_name}**")
        st.caption(f"Default branch: `{repo.default_branch}`")
        if st.button("Disconnect", width='stretch'):
            disconnect()
            st.rerun()

    st.divider()

    if st.button(
        "\U0001f5d1\ufe0f Clear All",
        width='stretch',
        help="Clear saved credentials (repo URL & PAT) and disconnect.",
    ):
        _clear_credentials()
        disconnect()
        st.rerun()

    st.caption(
        "Required PAT scopes: `Contents: Read & Write`, "
        "`Pull Requests: Read & Write`.  \n"
        "Credentials are saved locally in `.pl_credentials.json`."
    )

# ---------------------------------------------------------------------------
# Guard — nothing further until authenticated
# ---------------------------------------------------------------------------

if not st.session_state.authenticated:
    st.title("PrairieLearn Exam Scheduler")
    st.info(
        "Connect to a GitHub repository using the sidebar panel on the left "
        "to begin the scheduling workflow."
    )
    st.stop()

repo = st.session_state.repo

_title_col, _reset_col = st.columns([5, 1])
with _title_col:
    st.title("PrairieLearn Exam Scheduler")
    st.caption(
        f"Repository: **{repo.full_name}** "
        f"| Default branch: `{repo.default_branch}`"
    )
with _reset_col:
    st.write("")  # vertical spacing
    if st.button("Reset", help="Clear uploaded files and restart from Step 2."):
        st.session_state.terms_cache = []
        st.session_state.assessments_cache = {}
        st.session_state["_file_reset"] = st.session_state.get("_file_reset", 0) + 1
        for _k in list(st.session_state.keys()):
            if isinstance(_k, str) and _k.startswith(("date_", "start_", "end_", "sdc_")):
                del st.session_state[_k]
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Step 2 — Repository Navigation (cascading dropdowns)
# ---------------------------------------------------------------------------

st.header("Step 2 — Select Term and Assessment")

# Fetch and cache term list (invalidated on new connection)
if not st.session_state.terms_cache:
    with st.spinner("Loading course instances from GitHub..."):
        try:
            contents = repo.get_contents("courseInstances")
            if not isinstance(contents, list):
                contents = [contents]
            st.session_state.terms_cache = sorted(
                [c.name for c in contents if c.type == "dir"]
            )
        except GithubException as exc:
            st.error(
                f"Failed to read `courseInstances/`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

terms = st.session_state.terms_cache

if not terms:
    st.error("No term directories found inside `courseInstances/`.")
    st.stop()

col_term, col_assessment = st.columns(2)

with col_term:
    selected_term = st.selectbox(
        "Term",
        options=terms,
        help="Corresponds to a subdirectory of courseInstances/ (e.g. S26, F25).",
    )

# Fetch and cache assessments for the selected term
if selected_term not in st.session_state.assessments_cache:
    assessment_path = f"courseInstances/{selected_term}/assessments"
    with st.spinner(f"Loading assessments for term `{selected_term}`..."):
        try:
            assessment_contents = repo.get_contents(assessment_path)
            if not isinstance(assessment_contents, list):
                assessment_contents = [assessment_contents]
            st.session_state.assessments_cache[selected_term] = sorted(
                [c.name for c in assessment_contents if c.type == "dir"]
            )
        except UnknownObjectException:
            st.error(
                f"`{assessment_path}` does not exist in this repository. "
                "Verify the repository structure."
            )
            st.stop()
        except GithubException as exc:
            st.error(
                f"Failed to read `{assessment_path}`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

assessments = st.session_state.assessments_cache.get(selected_term, [])

with col_assessment:
    if not assessments:
        st.error(
            f"No assessment directories found inside "
            f"`courseInstances/{selected_term}/assessments/`."
        )
        st.stop()

    selected_assessment = st.selectbox(
        "Assessment",
        options=assessments,
        help="Corresponds to a subdirectory of assessments/ (e.g. pq1, hw2).",
    )

json_path = (
    f"courseInstances/{selected_term}/assessments/"
    f"{selected_assessment}/{JSON_FILE_NAME}"
)
st.caption(f"Target file: `{json_path}`")

st.divider()

# ---------------------------------------------------------------------------
# Step 3 — Roster Input (CSV upload or manual email list)
# ---------------------------------------------------------------------------

st.header("Step 3 — Upload Canvas Roster and Assign Time Slots")

roster_mode = st.radio(
    "Roster input method",
    options=["CSV Upload", "Manual Entry"],
    horizontal=True,
    help=(
        "CSV Upload: import students grouped by section from a Canvas export.  "
        "Manual Entry: paste a list of email addresses with a single shared time slot."
    ),
)

grouped: dict
sections: list
sdc_matched: list[dict] = []    # populated by SDC multiplier CSV upload
sdc_unmatched: list[str] = []   # SDC names not found in roster
df_raw: pd.DataFrame = pd.DataFrame()  # retain full roster for SDC cross-ref

if roster_mode == "CSV Upload":
    _fr = st.session_state.get("_file_reset", 0)
    csv_file = st.file_uploader(
        "Canvas Roster CSV",
        type=["csv"],
        key=f"csv_upload_{_fr}",
        help="Export from Canvas > Grades > Export (.csv).",
    )

    if csv_file is None:
        st.info("Upload a Canvas Roster CSV to continue.")
        st.stop()

    audit(
        "file_upload",
        detail=f"Canvas Roster CSV: {csv_file.name}",
        meta={"file_name": csv_file.name, "size_bytes": csv_file.size},
    )

    try:
        df_raw = load_csv_dataframe(csv_file)
    except ValueError as exc:
        st.error(f"CSV Error: {exc}")
        st.stop()

    if df_raw.empty:
        st.error("The uploaded CSV contains no rows.")
        st.stop()

    csv_columns = df_raw.columns.tolist()

    col_sec, col_email, col_name = st.columns(3)

    with col_sec:
        section_col_guess = next(
            (c for c in csv_columns if "section" in c.lower()), csv_columns[0]
        )
        section_col = st.selectbox(
            "Section column",
            options=csv_columns,
            index=csv_columns.index(section_col_guess),
            help="The column that identifies which section a student belongs to.",
        )

    with col_email:
        email_col_guess = next(
            (
                c
                for c in csv_columns
                if "email" in c.lower()
                or "sis login" in c.lower()
                or "login id" in c.lower()
            ),
            csv_columns[0],
        )
        email_col = st.selectbox(
            "Email / UID column",
            options=csv_columns,
            index=csv_columns.index(email_col_guess),
            help="The column that contains the student email or SIS Login ID.",
        )

    with col_name:
        name_col_guess = next(
            (c for c in csv_columns if "name" in c.lower() and "section" not in c.lower()),
            csv_columns[0],
        )
        name_col = st.selectbox(
            "Student name column",
            options=csv_columns,
            index=csv_columns.index(name_col_guess),
            help="Used for SDC multiplier cross-referencing by name.",
        )

    if section_col == email_col:
        st.warning("Section column and Email column must be different.")
        st.stop()

    # Parse and group students
    df = df_raw[[section_col, email_col]].copy()
    df[email_col] = df[email_col].astype(str).str.strip()
    df = df[df[email_col].notna() & (df[email_col] != "") & (df[email_col] != "nan")]
    grouped = df.groupby(section_col)[email_col].apply(list).to_dict()

    # Merge ", SDC" sub-sections into their parent section
    grouped = merge_sdc_sections(grouped)
    sections = sorted(grouped.keys())

    if not sections:
        st.error(
            "No sections were found after parsing the CSV. "
            "Check the column mapping."
        )
        st.stop()

    st.success(
        f"Found **{len(sections)} section(s)** across **{len(df)} student(s)**."
    )

    # ---- SDC Multiplier CSV upload ----
    st.divider()
    st.subheader("SDC / Accommodations (Optional)")
    st.caption(
        "Upload an SDC multiplier CSV to pull accommodated students out of "
        "their regular sections and assign extended time. The CSV should have "
        "columns for student name and time multiplier."
    )

    sdc_csv = st.file_uploader(
        "SDC Multiplier CSV",
        type=["csv"],
        key=f"sdc_csv_uploader_{_fr}",
        help="Two columns: Student name + Time multiplier (e.g. 1.5, 2).",
    )

    if sdc_csv is not None:
        audit(
            "file_upload",
            detail=f"SDC Multiplier CSV: {sdc_csv.name}",
            meta={"file_name": sdc_csv.name, "size_bytes": sdc_csv.size},
        )
        try:
            sdc_df = load_csv_dataframe(sdc_csv)
        except ValueError as exc:
            st.error(f"SDC CSV Error: {exc}")
            sdc_df = pd.DataFrame()

        if not sdc_df.empty:
            sdc_columns = sdc_df.columns.tolist()
            sdc_col1, sdc_col2 = st.columns(2)
            with sdc_col1:
                sdc_name_guess = next(
                    (c for c in sdc_columns if "student" in c.lower() or "name" in c.lower()),
                    sdc_columns[0],
                )
                sdc_name_col = st.selectbox(
                    "SDC name column",
                    options=sdc_columns,
                    index=sdc_columns.index(sdc_name_guess),
                    key="sdc_name_col",
                )
            with sdc_col2:
                sdc_mult_guess = next(
                    (c for c in sdc_columns if "multi" in c.lower() or "time" in c.lower()),
                    sdc_columns[-1],
                )
                sdc_mult_col = st.selectbox(
                    "Multiplier column",
                    options=sdc_columns,
                    index=sdc_columns.index(sdc_mult_guess),
                    key="sdc_mult_col",
                )

            # Cross-reference by name
            sdc_matched, sdc_unmatched = match_sdc_to_roster(
                sdc_df, df_raw,
                sdc_name_col, sdc_mult_col,
                name_col, email_col,
            )

            if sdc_matched:
                sdc_uid_set = {m["uid"] for m in sdc_matched}
                # Remove SDC UIDs from all regular sections
                for sec in list(grouped.keys()):
                    grouped[sec] = [u for u in grouped[sec] if u not in sdc_uid_set]
                # Remove empty sections
                grouped = {s: uids for s, uids in grouped.items() if uids}
                sections = sorted(grouped.keys())

                mult_summary = {}
                for m in sdc_matched:
                    mult_summary.setdefault(m["multiplier"], []).append(m["name"])
                summary_parts = [
                    f"{mult}x: {len(names)} student(s)"
                    for mult, names in sorted(mult_summary.items())
                ]
                st.success(
                    f"Matched **{len(sdc_matched)} SDC student(s)** "
                    f"({', '.join(summary_parts)}). "
                    "They have been removed from regular sections."
                )

            if sdc_unmatched:
                st.warning(
                    f"{len(sdc_unmatched)} SDC name(s) could not be matched "
                    f"to the roster: {', '.join(sdc_unmatched)}"
                )

else:
    # Manual Entry mode — no section concept, one shared time slot
    raw_input = st.text_area(
        "Student emails / UIDs",
        height=200,
        placeholder="student1@ucdavis.edu\nstudent2@ucdavis.edu\n...",
        help="Enter one email address (or PrairieLearn UID) per line.",
    )

    emails: list[str] = [
        line.strip()
        for line in raw_input.splitlines()
        if line.strip()
    ]

    if not emails:
        st.info("Enter at least one student email to continue.")
        st.stop()

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_emails: list[str] = []
    for e in emails:
        if e not in seen:
            seen.add(e)
            unique_emails.append(e)

    if len(unique_emails) < len(emails):
        st.warning(
            f"{len(emails) - len(unique_emails)} duplicate(s) removed. "
            f"Using {len(unique_emails)} unique address(es)."
        )

    _MANUAL_SECTION = "Manual"
    grouped = {_MANUAL_SECTION: unique_emails}
    sections = [_MANUAL_SECTION]

    st.success(f"**{len(unique_emails)} student(s)** will share one time slot.")

st.divider()

# ---------------------------------------------------------------------------
# Step 4 — Per-section time slot configuration
# ---------------------------------------------------------------------------

st.subheader("Time Slot Assignment")

# Timezone selector — must be declared before the section loop so that
# each row can display the DST-resolved offset for its chosen date.
_tz_labels = [label for label, _ in COMMON_TIMEZONES]
_tz_names  = {label: name for label, name in COMMON_TIMEZONES}

col_tz_sel, col_tz_info = st.columns([3, 3])
with col_tz_sel:
    selected_tz_label = st.selectbox(
        "Timezone",
        options=_tz_labels,
        index=0,
        help=(
            "All times are entered in this timezone. "
            "Daylight Saving Time is resolved automatically per date."
        ),
    )
selected_tz_name = _tz_names[selected_tz_label]

with col_tz_info:
    _today_label = get_tz_label(selected_tz_name, datetime.date.today())
    st.info(
        f"Today's active offset: **{_today_label}**  \n"
        "Times are written to JSON as local wall-clock time (no UTC suffix). "
        "The DST column below reflects the offset on each section's exam date."
    )

st.caption(
    "Configure the exam date and start/end times for each section. "
    "Expand a section row to review the student list."
)

# Table header row
hdr0, hdr1, hdr2, hdr3, hdr4 = st.columns([2.2, 1.8, 1.3, 1.3, 1.4])
if roster_mode == "CSV Upload":
    hdr0.markdown("**Section**")
else:
    hdr0.markdown("**Students**")
hdr1.markdown("**Date**")
hdr2.markdown("**Start**")
hdr3.markdown("**End**")
hdr4.markdown("**DST Offset**")
st.divider()

slot_configs: dict[str, dict] = {}

for section in sections:
    col0, col1, col2, col3, col4 = st.columns([2.2, 1.8, 1.3, 1.3, 1.4])

    with col0:
        student_count = len(grouped[section])
        expander_label = (
            f"{student_count} students"
            if section == "Manual"
            else f"{section}  ({student_count} students)"
        )
        with st.expander(expander_label):
            st.dataframe(
                pd.DataFrame(grouped[section], columns=["Email / UID"]),
                width='stretch',
                hide_index=True,
            )

    with col1:
        _date_sel = st.selectbox(
            "Date",
            options=_DATE_LABELS,
            index=_DEFAULT_DATE_IDX,
            key=f"date_{section}",
            label_visibility="collapsed",
        )
        chosen_date = _DATE_VALUES[_DATE_LABELS.index(_date_sel)]

    with col2:
        _start_sel = st.selectbox(
            "Start",
            options=_TIME_LABELS,
            index=_DEFAULT_START_IDX,
            key=f"start_{section}",
            label_visibility="collapsed",
        )
        chosen_start = _TIME_VALUES[_TIME_LABELS.index(_start_sel)]

    with col3:
        _end_sel = st.selectbox(
            "End",
            options=_TIME_LABELS,
            index=_DEFAULT_END_IDX,
            key=f"end_{section}",
            label_visibility="collapsed",
        )
        chosen_end = _TIME_VALUES[_TIME_LABELS.index(_end_sel)]

    with col4:
        # Resolve DST for this section's specific exam date
        tz_label = get_tz_label(selected_tz_name, chosen_date)
        st.markdown(f"<br><code>{tz_label}</code>", unsafe_allow_html=True)

    slot_configs[section] = {
        "date": chosen_date,
        "start": chosen_start,
        "end": chosen_end,
    }

# ---------------------------------------------------------------------------
# SDC Configuration (only shown when SDC students matched from multiplier CSV)
# ---------------------------------------------------------------------------

sdc_groups: list[dict] | None = None

if sdc_matched:
    st.divider()
    st.subheader("SDC / Accommodations — Global Window")

    multiplier_groups = group_sdc_by_multiplier(sdc_matched)
    total_sdc = sum(len(uids) for uids in multiplier_groups.values())
    group_labels = ", ".join(
        f"{m}x ({len(uids)})" for m, uids in sorted(multiplier_groups.items())
    )
    st.caption(
        f"{total_sdc} SDC student(s) in {len(multiplier_groups)} group(s): "
        f"{group_labels}."
    )

    # --- Derive timing parameters from section slot configs ---
    _section_starts = [cfg["start"] for cfg in slot_configs.values()]
    _section_ends = [cfg["end"] for cfg in slot_configs.values()]
    earliest_start = min(_section_starts) if _section_starts else datetime.time(10, 0)
    latest_start_default = max(_section_starts) if _section_starts else datetime.time(10, 0)

    # Base exam duration (minutes) derived from regular section configs
    _first_cfg = next(iter(slot_configs.values()), None)
    if _first_cfg:
        _dur_delta = (
            datetime.datetime.combine(datetime.date.today(), _first_cfg["end"])
            - datetime.datetime.combine(datetime.date.today(), _first_cfg["start"])
        )
        base_exam_min = max(int(_dur_delta.total_seconds() / 60), 1)
    else:
        base_exam_min = SDC_BASE_EXAM_MIN

    sdc_col1, sdc_col2 = st.columns(2)
    with sdc_col1:
        sdc_date = st.date_input(
            "SDC exam date",
            value=_DEFAULT_DATE,
            key="sdc_date",
        )
        st.markdown(
            f"**Window opens:** `{earliest_start.strftime('%H:%M')}`  \n"
            f"*(earliest section start — auto-detected)*"
        )
    with sdc_col2:
        last_exam_start = st.time_input(
            "Last exam start time",
            value=latest_start_default,
            key="sdc_last_exam_start",
            step=300,
            help="The start time of the last regular exam session of the day.",
        )

    # DST offset for SDC date
    sdc_tz_label = get_tz_label(selected_tz_name, sdc_date)
    st.caption(
        f"SDC date DST offset: `{sdc_tz_label}` "
        f"| Base exam duration: **{base_exam_min} min**"
    )

    # Per-group summary with auto-computed end times
    st.markdown("**Per-group schedule:**")
    for mult in sorted(multiplier_groups.keys()):
        uids = multiplier_groups[mult]
        computed_min = int(base_exam_min * mult)
        _end_dt = (
            datetime.datetime.combine(sdc_date, last_exam_start)
            + datetime.timedelta(minutes=computed_min)
        )
        st.write(
            f"- **{mult}x** — {len(uids)} student(s), "
            f"timeLimitMin = {computed_min} min, "
            f"window closes `{_end_dt.strftime('%H:%M')}`"
        )

    with st.expander(f"SDC student list ({total_sdc} students)"):
        sdc_display = pd.DataFrame(
            [(m["name"], m["uid"], m["multiplier"]) for m in sdc_matched],
            columns=["Name", "Email / UID", "Multiplier"],
        )
        st.dataframe(sdc_display, width='stretch', hide_index=True)

    # Build sdc_groups list (one dict per multiplier group)
    sdc_groups = []
    for mult in sorted(multiplier_groups.keys()):
        computed_min = int(base_exam_min * mult)
        _end_dt = (
            datetime.datetime.combine(sdc_date, last_exam_start)
            + datetime.timedelta(minutes=computed_min)
        )
        sdc_groups.append({
            "uids": multiplier_groups[mult],
            "date": sdc_date,
            "start": earliest_start,
            "end": _end_dt.time(),
            "end_date": _end_dt.date(),
            "timeLimitMin": computed_min,
            "multiplier": mult,
        })

st.divider()

# ---------------------------------------------------------------------------
# Step 5 — Validation and Pull Request submission
# ---------------------------------------------------------------------------

st.header("Step 4 — Review and Submit Pull Request")

# Validate time ordering before any API calls
time_errors = [
    f"**{s}**: Start time must be earlier than End time."
    for s, cfg in slot_configs.items()
    if cfg["start"] >= cfg["end"]
]
if sdc_groups:
    for _sg in sdc_groups:
        _sg_start_dt = datetime.datetime.combine(_sg["date"], _sg["start"])
        _sg_end_dt = datetime.datetime.combine(
            _sg.get("end_date", _sg["date"]), _sg["end"]
        )
        if _sg_start_dt >= _sg_end_dt:
            time_errors.append(
                f"**SDC ({_sg['multiplier']}x)**: Window open must be earlier than close."
            )
            break
if time_errors:
    for err in time_errors:
        st.error(err)
    st.stop()

# PR preview
with st.expander("Pull Request preview"):
    pr_body_preview = build_pr_body(
        selected_assessment, sections, slot_configs, grouped,
        selected_tz_name, sdc_groups=sdc_groups,
    )
    st.markdown(pr_body_preview)

if st.button(
    "Generate and Create Pull Request",
    type="primary",
    width='stretch',
):
    allow_access = build_allow_access(
        sections, grouped, slot_configs, sdc_groups=sdc_groups,
    )

    # --- 1. Fetch remote infoAssessment.json ---
    with st.spinner(f"Fetching `{json_path}` from GitHub..."):
        try:
            _fetched = repo.get_contents(json_path)
            # get_contents returns a list for directories; for a file path it
            # always returns a single ContentFile — unwrap defensively.
            file_obj = _fetched[0] if isinstance(_fetched, list) else _fetched
        except UnknownObjectException:
            st.error(
                f"`{json_path}` was not found in the repository. "
                "Verify the selected Term and Assessment."
            )
            st.stop()
        except GithubException as exc:
            st.error(
                f"GitHub API error while fetching the target file: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    # --- 2. Parse remote JSON ---
    try:
        raw_text = file_obj.decoded_content.decode("utf-8")
        base_json: dict = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        st.error(
            f"The remote `{JSON_FILE_NAME}` could not be parsed as JSON: {exc}"
        )
        st.stop()

    # --- 3. Overwrite allowAccess ---
    base_json["allowAccess"] = allow_access
    updated_content = json.dumps(base_json, indent=2, ensure_ascii=False)

    # --- 4. Create a new branch ---
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    branch_name = f"update-access-{selected_assessment}-{timestamp}"

    with st.spinner(f"Creating branch `{branch_name}`..."):
        try:
            default_ref = repo.get_branch(repo.default_branch)
            base_sha = default_ref.commit.sha
            repo.create_git_ref(
                ref=f"refs/heads/{branch_name}", sha=base_sha
            )
        except GithubException as exc:
            st.error(
                f"Failed to create branch `{branch_name}`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    # --- 5. Commit the modified file ---
    commit_message = (
        f"Auto-schedule: update allowAccess rules for {selected_assessment}"
    )
    with st.spinner("Committing updated infoAssessment.json..."):
        try:
            repo.update_file(
                path=json_path,
                message=commit_message,
                content=updated_content,
                sha=file_obj.sha,
                branch=branch_name,
            )
        except GithubException as exc:
            st.error(
                f"Failed to commit to branch `{branch_name}`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    # --- 6. Open Pull Request ---
    pr_title = (
        f"Auto-schedule: Update access rules for {selected_assessment}"
    )
    pr_body = build_pr_body(
        selected_assessment, sections, slot_configs, grouped,
        selected_tz_name, sdc_groups=sdc_groups,
    )
    with st.spinner("Creating Pull Request..."):
        try:
            pr = repo.create_pull(
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=repo.default_branch,
            )
        except GithubException as exc:
            st.error(
                f"Failed to create the Pull Request: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    # --- 7. Success ---
    st.success("Pull Request created successfully.")
    st.markdown(f"**Pull Request URL:** {pr.html_url}")

    audit(
        "submission",
        detail=f"PR #{pr.number} for {selected_assessment}",
        meta={
            "repo": repo.full_name,
            "branch": branch_name,
            "pr_number": pr.number,
            "pr_url": pr.html_url,
            "assessment": selected_assessment,
            "term": selected_term,
            "sections": len(sections),
            "sdc_groups": len(sdc_groups) if sdc_groups else 0,
        },
    )

    with st.expander("Committed infoAssessment.json — full preview", expanded=True):
        st.code(updated_content, language="json")

