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

import pandas as pd
import pytz
import streamlit as st
from github import Github, GithubException, UnknownObjectException

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSON_FILE_NAME = "infoAssessment.json"

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


def build_allow_access(
    sections: list[str],
    grouped: dict[str, list[str]],
    slot_configs: dict[str, dict],
) -> list[dict]:
    """Construct the allowAccess array from the per-section schedule."""
    entries = []
    for section in sections:
        cfg = slot_configs[section]
        entries.append(
            {
                "mode": "Exam",
                "startDate": combine_datetime(cfg["date"], cfg["start"]),
                "endDate": combine_datetime(cfg["date"], cfg["end"]),
                "uids": sorted(grouped[section]),
            }
        )
    return entries


def build_pr_body(
    assessment_id: str,
    sections: list[str],
    slot_configs: dict[str, dict],
    grouped: dict,
    tz_name: str = "America/Los_Angeles",
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

    repo_url_input = st.text_input(
        "Repository URL",
        placeholder="https://github.com/owner/pl-course-repo",
        help="Full URL of the PrairieLearn course repository on GitHub.",
    )

    pat_input = st.text_input(
        "Personal Access Token (PAT)",
        type="password",
        help=(
            "A GitHub PAT with Contents (Read & Write) and "
            "Pull Requests (Read & Write) permissions."
        ),
    )

    connect_clicked = st.button(
        "Connect", type="primary", use_container_width=True
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
        if st.button("Disconnect", use_container_width=True):
            disconnect()
            st.rerun()

    st.divider()
    st.caption(
        "Required PAT scopes: `Contents: Read & Write`, "
        "`Pull Requests: Read & Write`."
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

st.title("PrairieLearn Exam Scheduler")
st.caption(
    f"Repository: **{repo.full_name}** "
    f"| Default branch: `{repo.default_branch}`"
)

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

if roster_mode == "CSV Upload":
    csv_file = st.file_uploader(
        "Canvas Roster CSV",
        type=["csv"],
        help="Export from Canvas > Grades > Export (.csv).",
    )

    if csv_file is None:
        st.info("Upload a Canvas Roster CSV to continue.")
        st.stop()

    try:
        df_raw = load_csv_dataframe(csv_file)
    except ValueError as exc:
        st.error(f"CSV Error: {exc}")
        st.stop()

    if df_raw.empty:
        st.error("The uploaded CSV contains no rows.")
        st.stop()

    csv_columns = df_raw.columns.tolist()

    col_sec, col_email = st.columns(2)

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

    if section_col == email_col:
        st.warning("Section column and Email column must be different.")
        st.stop()

    # Parse and group students
    df = df_raw[[section_col, email_col]].copy()
    df[email_col] = df[email_col].astype(str).str.strip()
    df = df[df[email_col].notna() & (df[email_col] != "") & (df[email_col] != "nan")]
    grouped = df.groupby(section_col)[email_col].apply(list).to_dict()
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
default_date = datetime.date.today()

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
                use_container_width=True,
                hide_index=True,
            )

    with col1:
        chosen_date = st.date_input(
            "Date",
            value=default_date,
            key=f"date_{section}",
            label_visibility="collapsed",
        )

    with col2:
        chosen_start = st.time_input(
            "Start",
            value=datetime.time(10, 0),
            key=f"start_{section}",
            label_visibility="collapsed",
            step=300,
        )

    with col3:
        chosen_end = st.time_input(
            "End",
            value=datetime.time(10, 50),
            key=f"end_{section}",
            label_visibility="collapsed",
            step=300,
        )

    with col4:
        # Resolve DST for this section's specific exam date
        tz_label = get_tz_label(selected_tz_name, chosen_date)
        st.markdown(f"<br><code>{tz_label}</code>", unsafe_allow_html=True)

    slot_configs[section] = {
        "date": chosen_date,
        "start": chosen_start,
        "end": chosen_end,
    }

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
if time_errors:
    for err in time_errors:
        st.error(err)
    st.stop()

# PR preview
with st.expander("Pull Request preview"):
    pr_body_preview = build_pr_body(
        selected_assessment, sections, slot_configs, grouped, selected_tz_name
    )
    st.markdown(pr_body_preview)

if st.button(
    "Generate and Create Pull Request",
    type="primary",
    use_container_width=True,
):
    allow_access = build_allow_access(
        sections, grouped, slot_configs
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
        selected_assessment, sections, slot_configs, grouped, selected_tz_name
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

    with st.expander("Committed infoAssessment.json — full preview", expanded=True):
        st.code(updated_content, language="json")

