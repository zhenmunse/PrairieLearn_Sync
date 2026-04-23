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

import copy
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
    "hydrated_target_key": "",
    "source_file_sha": "",
    "base_json_snapshot": {},
    "original_allow_access": [],
    "passthrough_allow_access": [],
    "original_editor_rules": [],
    "editable_rules": [],
    "next_rule_seq": 1,
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
    Generate a list of dates formatted as 'YYYY-MM-DD (Mon)' for selectbox.
    Includes 3 days in the past + num_days future to account for timezone
    differences between server and client. Returns (labels, date_objects).
    """
    today = datetime.date.today()
    labels: list[str] = []
    dates: list[datetime.date] = []
    # Start 3 days ago to cover timezone offsets (UTC vs. local time)
    start_date = today - datetime.timedelta(days=3)
    total_days = 3 + num_days
    for i in range(total_days):
        d = start_date + datetime.timedelta(days=i)
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


def _parse_iso_datetime(value: str | None) -> tuple[datetime.date, datetime.time]:
    """Parse an ISO datetime string into (date, time)."""
    default_date = datetime.date.today()
    default_time = datetime.time(10, 0)

    text = str(value or "").strip()
    if not text:
        return default_date, default_time

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return dt.date(), dt.time().replace(microsecond=0)
        except ValueError:
            continue

    try:
        dt2 = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt2.date(), dt2.time().replace(tzinfo=None, microsecond=0)
    except ValueError:
        return default_date, default_time


def _normalize_uids(raw_uids: list[str] | tuple[str, ...] | None) -> list[str]:
    """Clean and deduplicate UID values while preserving input order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_uids or []:
        uid = str(raw).strip()
        if not uid or uid.lower() == "nan":
            continue
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def _is_session_allow_access_rule(rule: dict) -> bool:
    """Return True when an allowAccess entry represents a timed student session."""
    return bool(
        isinstance(rule, dict)
        and rule.get("startDate")
        and rule.get("endDate")
        and isinstance(rule.get("uids"), list)
    )


def _allow_access_rule_to_editor_rule(
    rule: dict,
    *,
    origin_id: str | None,
    rule_id: str,
) -> dict:
    """Convert one allowAccess session rule into editable UI state."""
    start_date, start_time = _parse_iso_datetime(rule.get("startDate"))
    end_date, end_time = _parse_iso_datetime(rule.get("endDate"))

    known_keys = {
        "comment",
        "credit",
        "timeLimitMin",
        "startDate",
        "endDate",
        "uids",
        "password",
        "showClosedAssessment",
        "showClosedAssessmentScore",
        "active",
    }

    duration_min = max(
        int(
            (
                datetime.datetime.combine(end_date, end_time)
                - datetime.datetime.combine(start_date, start_time)
            ).total_seconds()
            / 60
        ),
        1,
    )
    try:
        time_limit = int(rule.get("timeLimitMin", duration_min))
    except (TypeError, ValueError):
        time_limit = duration_min

    try:
        credit = int(rule.get("credit", 100))
    except (TypeError, ValueError):
        credit = 100

    return {
        "rule_id": rule_id,
        "origin_id": origin_id,
        "comment": str(rule.get("comment", "")).strip(),
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "uids": _normalize_uids(rule.get("uids", [])),
        "credit": credit,
        "timeLimitMin": max(time_limit, 1),
        "password": "" if rule.get("password") is None else str(rule.get("password")),
        "showClosedAssessment": bool(rule.get("showClosedAssessment", False)),
        "showClosedAssessmentScore": bool(rule.get("showClosedAssessmentScore", False)),
        "active": bool(rule.get("active", True)),
        "active_in_source": "active" in rule,
        "extras": {k: copy.deepcopy(v) for k, v in rule.items() if k not in known_keys},
    }


def _editor_rule_to_allow_access_rule(rule: dict) -> dict:
    """Convert editable UI state back into one allowAccess session rule."""
    out = {
        "comment": str(rule.get("comment", "")).strip(),
        "credit": int(rule.get("credit", 100)),
        "timeLimitMin": max(int(rule.get("timeLimitMin", 1)), 1),
        "startDate": combine_datetime(rule["start_date"], rule["start_time"]),
        "endDate": combine_datetime(rule["end_date"], rule["end_time"]),
        "uids": _normalize_uids(rule.get("uids", [])),
        "showClosedAssessment": bool(rule.get("showClosedAssessment", False)),
        "showClosedAssessmentScore": bool(rule.get("showClosedAssessmentScore", False)),
    }

    if rule.get("password"):
        out["password"] = str(rule["password"])

    if rule.get("active_in_source") or not bool(rule.get("active", True)):
        out["active"] = bool(rule.get("active", True))

    for k, v in rule.get("extras", {}).items():
        if k not in out:
            out[k] = copy.deepcopy(v)

    return out


def _split_allow_access_rules(
    allow_access: list[dict],
    *,
    rule_prefix: str,
) -> tuple[list[dict], list[dict]]:
    """Split allowAccess entries into editable session rules and passthrough rules."""
    editable: list[dict] = []
    passthrough: list[dict] = []

    for idx, rule in enumerate(allow_access):
        if _is_session_allow_access_rule(rule):
            editable.append(
                _allow_access_rule_to_editor_rule(
                    rule,
                    origin_id=f"orig-{idx}",
                    rule_id=f"{rule_prefix}-orig-{idx}",
                )
            )
        else:
            passthrough.append(copy.deepcopy(rule))

    return editable, passthrough


def _build_empty_editor_rule(rule_id: str) -> dict:
    """Create a blank editable session block."""
    return {
        "rule_id": rule_id,
        "origin_id": None,
        "comment": "",
        "start_date": _DEFAULT_DATE,
        "start_time": _TIME_VALUES[_DEFAULT_START_IDX],
        "end_date": _DEFAULT_DATE,
        "end_time": _TIME_VALUES[_DEFAULT_END_IDX],
        "uids": [],
        "credit": 100,
        "timeLimitMin": 50,
        "password": "",
        "showClosedAssessment": False,
        "showClosedAssessmentScore": False,
        "active": True,
        "active_in_source": False,
        "extras": {},
    }


def _session_window_label(rule: dict) -> str:
    """Render a stable [Start] - [End] label for one editable session rule."""
    start = combine_datetime(rule["start_date"], rule["start_time"])
    end = combine_datetime(rule["end_date"], rule["end_time"])
    return f"[{start}] - [{end}]"


def _uids_markdown(uids: list[str], max_items: int = 12) -> str:
    """Format a UID list for concise markdown output."""
    if not uids:
        return "`(none)`"
    clipped = uids[:max_items]
    rendered = ", ".join(f"`{u}`" for u in clipped)
    if len(uids) > max_items:
        rendered += f", ... (+{len(uids) - max_items} more)"
    return rendered


def _password_state(value: str) -> str:
    """Return a user-safe password state string for PR summaries."""
    return "set" if str(value or "").strip() else "empty"


def _calculate_access_diff(
    original_rules: list[dict],
    final_rules: list[dict],
) -> dict[str, list[str]]:
    """Compute Added / Modified / Deleted summary lines for editable rules."""
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []

    original_by_origin = {
        r["origin_id"]: r for r in original_rules if r.get("origin_id")
    }
    final_by_origin = {
        r["origin_id"]: r for r in final_rules if r.get("origin_id")
    }

    # New sessions
    for rule in final_rules:
        if rule.get("origin_id"):
            continue
        added.append(
            f"- Session {_session_window_label(rule)}: Added UIDs: "
            f"{_uids_markdown(rule['uids'])}"
        )

    for origin_id, old_rule in original_by_origin.items():
        new_rule = final_by_origin.get(origin_id)
        if new_rule is None:
            deleted.append(
                f"- Removed Session {_session_window_label(old_rule)} "
                f"with UIDs: {_uids_markdown(old_rule['uids'])}"
            )
            continue

        old_uids = set(old_rule["uids"])
        new_uids = set(new_rule["uids"])

        added_uids = sorted(new_uids - old_uids)
        removed_uids = sorted(old_uids - new_uids)

        if added_uids:
            added.append(
                f"- Session {_session_window_label(new_rule)}: Added UIDs: "
                f"{_uids_markdown(added_uids)}"
            )

        if removed_uids:
            deleted.append(
                f"- Removed UIDs: {_uids_markdown(removed_uids)} "
                f"from Session {_session_window_label(new_rule)}."
            )

        mods: list[str] = []
        if (
            old_rule["start_date"] != new_rule["start_date"]
            or old_rule["start_time"] != new_rule["start_time"]
            or old_rule["end_date"] != new_rule["end_date"]
            or old_rule["end_time"] != new_rule["end_time"]
        ):
            old_start = combine_datetime(old_rule["start_date"], old_rule["start_time"])
            old_end = combine_datetime(old_rule["end_date"], old_rule["end_time"])
            new_start = combine_datetime(new_rule["start_date"], new_rule["start_time"])
            new_end = combine_datetime(new_rule["end_date"], new_rule["end_time"])
            mods.append(
                f"window changed from [{old_start}] - [{old_end}] "
                f"to [{new_start}] - [{new_end}]"
            )

        if int(old_rule["timeLimitMin"]) != int(new_rule["timeLimitMin"]):
            mods.append(
                f"time limit changed from {old_rule['timeLimitMin']} "
                f"to {new_rule['timeLimitMin']}"
            )

        if _password_state(old_rule.get("password", "")) != _password_state(new_rule.get("password", "")):
            mods.append(
                f"password state changed from {_password_state(old_rule.get('password', ''))} "
                f"to {_password_state(new_rule.get('password', ''))}"
            )

        if mods:
            modified.append(
                f"- Session {_session_window_label(new_rule)}: " + "; ".join(mods) + "."
            )

    return {"added": added, "modified": modified, "deleted": deleted}


def _render_schedule_table(final_allow_access: list[dict], tz_name: str) -> str:
    """Render markdown table for full post-update allowAccess schedule."""
    session_entries = [r for r in final_allow_access if _is_session_allow_access_rule(r)]
    session_entries = sorted(
        session_entries,
        key=lambda r: (str(r.get("startDate", "")), str(r.get("comment", ""))),
    )

    rows: list[str] = []
    for rule in session_entries:
        start = str(rule.get("startDate", ""))
        end = str(rule.get("endDate", ""))
        date_for_tz, _ = _parse_iso_datetime(start)
        tz_label = get_tz_label(tz_name, date_for_tz)
        rows.append(
            f"| {rule.get('comment', '') or '(no comment)'} "
            f"| {len(rule.get('uids', []))} "
            f"| {start} "
            f"| {end} "
            f"| {rule.get('timeLimitMin', '')} "
            f"| {('set' if rule.get('password') else 'empty')} "
            f"| {tz_label} |"
        )

    if not rows:
        rows.append("| (none) | 0 | - | - | - | - | - |")

    return "\n".join(
        [
            "| Session | Students | Start | End | Time Limit | Password | Offset |",
            "|---------|----------|-------|-----|------------|----------|--------|",
        ]
        + rows
    )


def build_pr_body_with_diff(
    assessment_id: str,
    diff_summary: dict[str, list[str]],
    final_allow_access: list[dict],
    tz_name: str,
) -> str:
    """Build final PR body with required changelog section at the top."""
    added_lines = diff_summary.get("added") or ["- None."]
    modified_lines = diff_summary.get("modified") or ["- None."]
    deleted_lines = diff_summary.get("deleted") or ["- None."]

    table_md = _render_schedule_table(final_allow_access, tz_name)

    return "\n".join(
        [
            "## Access Rules Update Summary",
            "**Added:**",
            *added_lines,
            "**Modified:**",
            *modified_lines,
            "**Deleted:**",
            *deleted_lines,
            "",
            "---",
            "*Below is the complete new schedule generated by the system:*",
            "",
            table_md,
            "",
            f"Assessment: `{assessment_id}`",
            f"Timezone for display: `{tz_name}`",
            "",
            "Times are written to JSON as local wall-clock time (no UTC suffix).",
        ]
    )


def _merge_imported_rules(
    existing_rules: list[dict],
    imported_session_rules: list[dict],
    *,
    merge_mode: str,
    rule_prefix: str,
    next_rule_seq: int,
) -> tuple[list[dict], int]:
    """Apply append/merge behavior for imported CSV/manual generated sessions."""
    merged_rules = copy.deepcopy(existing_rules)

    for raw in imported_session_rules:
        imported = _allow_access_rule_to_editor_rule(
            raw,
            origin_id=None,
            rule_id=f"{rule_prefix}-new-{next_rule_seq}",
        )
        next_rule_seq += 1

        if merge_mode == "Merge by Session Window":
            found_idx = None
            for idx, rule in enumerate(merged_rules):
                if (
                    rule["comment"] == imported["comment"]
                    and rule["start_date"] == imported["start_date"]
                    and rule["start_time"] == imported["start_time"]
                    and rule["end_date"] == imported["end_date"]
                    and rule["end_time"] == imported["end_time"]
                ):
                    found_idx = idx
                    break

            if found_idx is not None:
                existing_uids = set(merged_rules[found_idx]["uids"])
                existing_uids.update(imported["uids"])
                merged_rules[found_idx]["uids"] = sorted(existing_uids)
                continue

        merged_rules.append(imported)

    return merged_rules, next_rule_seq


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
        st.session_state.hydrated_target_key = ""
        st.session_state.source_file_sha = ""
        st.session_state.base_json_snapshot = {}
        st.session_state.original_allow_access = []
        st.session_state.passthrough_allow_access = []
        st.session_state.original_editor_rules = []
        st.session_state.editable_rules = []
        st.session_state.next_rule_seq = 1
        for _k in list(st.session_state.keys()):
            if isinstance(_k, str) and _k.startswith(
                (
                    "date_",
                    "start_",
                    "end_",
                    "sdc_",
                    "edit_",
                    "append_",
                    "import_",
                )
            ):
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
# Step 3 — Read & Hydrate Existing allowAccess
# ---------------------------------------------------------------------------

target_key = f"{repo.full_name}:{repo.default_branch}:{json_path}"
rule_prefix = f"{selected_term}-{selected_assessment}".replace("/", "-")

if st.session_state.hydrated_target_key != target_key:
    with st.spinner(f"Loading `{json_path}` from `{repo.default_branch}`..."):
        try:
            fetched = repo.get_contents(json_path, ref=repo.default_branch)
            file_obj = fetched[0] if isinstance(fetched, list) else fetched
        except UnknownObjectException:
            st.error(
                f"`{json_path}` was not found in the repository. "
                "Verify the selected Term and Assessment."
            )
            st.stop()
        except GithubException as exc:
            st.error(
                f"GitHub API error while fetching `{json_path}`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    try:
        raw_text = file_obj.decoded_content.decode("utf-8")
        base_json_snapshot: dict = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        st.error(
            f"The remote `{JSON_FILE_NAME}` could not be parsed as JSON: {exc}"
        )
        st.stop()

    original_allow_access = base_json_snapshot.get("allowAccess", [])
    if not isinstance(original_allow_access, list):
        st.error("`allowAccess` is not a JSON array in the remote file.")
        st.stop()

    editable_rules, passthrough_rules = _split_allow_access_rules(
        original_allow_access,
        rule_prefix=rule_prefix,
    )

    st.session_state.hydrated_target_key = target_key
    st.session_state.source_file_sha = file_obj.sha
    st.session_state.base_json_snapshot = copy.deepcopy(base_json_snapshot)
    st.session_state.original_allow_access = copy.deepcopy(original_allow_access)
    st.session_state.passthrough_allow_access = copy.deepcopy(passthrough_rules)
    st.session_state.original_editor_rules = copy.deepcopy(editable_rules)
    st.session_state.editable_rules = copy.deepcopy(editable_rules)
    st.session_state.next_rule_seq = max(1, len(editable_rules) + 1)

st.header("Step 3 — Edit Existing Access Rules")
st.caption(
    "Loaded from the repository default branch. You can fully edit each session "
    "(time window, UIDs, password, limits), delete blocks, or append new blocks."
)

summary_col1, summary_col2, summary_col3 = st.columns(3)
summary_col1.metric("Editable Sessions", len(st.session_state.editable_rules))
summary_col2.metric("Passthrough Rules", len(st.session_state.passthrough_allow_access))
summary_col3.metric("Source Branch", repo.default_branch)

reload_col, add_col = st.columns([1, 1])
with reload_col:
    if st.button("Reload Latest From Main", help="Discard local edits and re-hydrate from default branch."):
        st.session_state.hydrated_target_key = ""
        st.rerun()
with add_col:
    if st.button("Add Empty Session Block"):
        new_id = f"{rule_prefix}-new-{st.session_state.next_rule_seq}"
        st.session_state.next_rule_seq += 1
        st.session_state.editable_rules.append(_build_empty_editor_rule(new_id))
        st.rerun()

_tz_labels = [label for label, _ in COMMON_TIMEZONES]
_tz_names = {label: name for label, name in COMMON_TIMEZONES}
selected_tz_label = st.selectbox(
    "Timezone",
    options=_tz_labels,
    index=0,
    key=f"edit_timezone_{rule_prefix}",
    help=(
        "Used for display and PR table offsets. Times are still written to JSON "
        "as local wall-clock timestamps without UTC suffix."
    ),
)
selected_tz_name = _tz_names[selected_tz_label]
st.info(f"Today's active offset: **{get_tz_label(selected_tz_name, datetime.date.today())}**")

updated_rules: list[dict] = []
for idx, rule in enumerate(st.session_state.editable_rules, start=1):
    rid = rule["rule_id"]
    label = rule.get("comment") or f"Session {idx}"
    with st.expander(f"{idx}. {label}", expanded=False):
        delete_clicked = st.button("Delete This Session", key=f"edit_delete_{rid}")

        c1, c2 = st.columns(2)
        comment = c1.text_input(
            "Comment",
            value=rule.get("comment", ""),
            key=f"edit_comment_{rid}",
        ) or ""
        password = c2.text_input(
            "Password (optional)",
            value=rule.get("password", ""),
            key=f"edit_password_{rid}",
        ) or ""

        c3, c4, c5, c6 = st.columns(4)
        start_date = c3.date_input(
            "Start date",
            value=rule["start_date"],
            key=f"edit_start_date_{rid}",
        )
        start_time = c4.time_input(
            "Start time",
            value=rule["start_time"],
            step=300,
            key=f"edit_start_time_{rid}",
        )
        end_date = c5.date_input(
            "End date",
            value=rule["end_date"],
            key=f"edit_end_date_{rid}",
        )
        end_time = c6.time_input(
            "End time",
            value=rule["end_time"],
            step=300,
            key=f"edit_end_time_{rid}",
        )

        c7, c8, c9 = st.columns(3)
        credit = c7.number_input(
            "Credit",
            min_value=0,
            max_value=100,
            value=int(rule.get("credit", 100)),
            step=1,
            key=f"edit_credit_{rid}",
        )
        time_limit_min = c8.number_input(
            "timeLimitMin",
            min_value=1,
            value=int(rule.get("timeLimitMin", 50)),
            step=1,
            key=f"edit_time_limit_{rid}",
        )
        active = c9.checkbox(
            "Active",
            value=bool(rule.get("active", True)),
            key=f"edit_active_{rid}",
        )

        c10, c11 = st.columns(2)
        show_closed = c10.checkbox(
            "showClosedAssessment",
            value=bool(rule.get("showClosedAssessment", False)),
            key=f"edit_show_closed_{rid}",
        )
        show_closed_score = c11.checkbox(
            "showClosedAssessmentScore",
            value=bool(rule.get("showClosedAssessmentScore", False)),
            key=f"edit_show_closed_score_{rid}",
        )

        uid_text = st.text_area(
            "UIDs (one per line)",
            value="\n".join(rule.get("uids", [])),
            height=140,
            key=f"edit_uids_{rid}",
        )
        parsed_uids = _normalize_uids(uid_text.splitlines())

        tz_for_row = get_tz_label(selected_tz_name, start_date)
        st.caption(f"Row offset: `{tz_for_row}` | UIDs: **{len(parsed_uids)}**")

        if delete_clicked:
            continue

        updated_rules.append(
            {
                "rule_id": rid,
                "origin_id": rule.get("origin_id"),
                "comment": comment.strip(),
                "start_date": start_date,
                "start_time": start_time,
                "end_date": end_date,
                "end_time": end_time,
                "uids": parsed_uids,
                "credit": int(credit),
                "timeLimitMin": int(time_limit_min),
                "password": password,
                "showClosedAssessment": bool(show_closed),
                "showClosedAssessmentScore": bool(show_closed_score),
                "active": bool(active),
                "active_in_source": bool(rule.get("active_in_source", False)),
                "extras": copy.deepcopy(rule.get("extras", {})),
            }
        )

st.session_state.editable_rules = updated_rules

st.divider()

# ---------------------------------------------------------------------------
# Step 4 — Optional Append / Merge from CSV or Manual
# ---------------------------------------------------------------------------

st.header("Step 4 — Append or Merge New Sessions")
st.caption(
    "Use CSV Upload or Manual Entry to generate additional session blocks, "
    "then append/merge them into the editable schedule above."
)

append_mode = st.radio(
    "Input method",
    options=["CSV Upload", "Manual Entry"],
    horizontal=True,
    key=f"append_mode_{rule_prefix}",
)

append_grouped: dict[str, list[str]] = {}
append_sections: list[str] = []

if append_mode == "CSV Upload":
    csv_file = st.file_uploader(
        "Canvas Roster CSV",
        type=["csv"],
        key=f"append_csv_{st.session_state.get('_file_reset', 0)}_{rule_prefix}",
    )

    if csv_file is not None:
        audit(
            "file_upload",
            detail=f"Canvas Roster CSV: {csv_file.name}",
            meta={"file_name": csv_file.name, "size_bytes": csv_file.size},
        )
        try:
            df_raw = load_csv_dataframe(csv_file)
        except ValueError as exc:
            st.error(f"CSV Error: {exc}")
            df_raw = pd.DataFrame()

        if not df_raw.empty:
            csv_columns = df_raw.columns.tolist()
            col_sec, col_email = st.columns(2)

            with col_sec:
                section_col_guess = next(
                    (c for c in csv_columns if "section" in c.lower()),
                    csv_columns[0],
                )
                section_col = st.selectbox(
                    "Section column",
                    options=csv_columns,
                    index=csv_columns.index(section_col_guess),
                    key=f"append_section_col_{rule_prefix}",
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
                    key=f"append_email_col_{rule_prefix}",
                )

            if section_col == email_col:
                st.warning("Section column and Email column must be different.")
            else:
                df = df_raw[[section_col, email_col]].copy()
                df[email_col] = df[email_col].astype(str).str.strip()
                df = df[
                    df[email_col].notna()
                    & (df[email_col] != "")
                    & (df[email_col] != "nan")
                ]
                grouped_raw = df.groupby(section_col)[email_col].apply(list).to_dict()
                append_grouped = {
                    str(k): [str(u).strip() for u in v]
                    for k, v in grouped_raw.items()
                }
                append_grouped = merge_sdc_sections(append_grouped)
                append_sections = sorted(append_grouped.keys())

                if append_sections:
                    st.success(
                        f"Prepared {len(append_sections)} section(s) and {len(df)} student row(s) for import."
                    )
                else:
                    st.warning("No section data found in CSV after parsing.")
else:
    raw_input = st.text_area(
        "Student emails / UIDs",
        height=160,
        key=f"append_manual_uids_{rule_prefix}",
        placeholder="student1@ucdavis.edu\nstudent2@ucdavis.edu\n...",
    )

    emails = _normalize_uids(raw_input.splitlines())
    if emails:
        append_grouped = {"Manual": emails}
        append_sections = ["Manual"]
        st.success(f"Prepared {len(emails)} student(s) for import.")

generated_session_rules: list[dict] = []
if append_sections:
    st.markdown("**Configure imported session windows**")
    append_slot_configs: dict[str, dict] = {}

    hdr0, hdr1, hdr2, hdr3, hdr4 = st.columns([2.2, 1.8, 1.3, 1.3, 1.4])
    hdr0.markdown("**Section**")
    hdr1.markdown("**Date**")
    hdr2.markdown("**Start**")
    hdr3.markdown("**End**")
    hdr4.markdown("**DST Offset**")

    for section in append_sections:
        col0, col1, col2, col3, col4 = st.columns([2.2, 1.8, 1.3, 1.3, 1.4])

        with col0:
            with st.expander(f"{section} ({len(append_grouped[section])} students)"):
                st.dataframe(
                    pd.DataFrame(append_grouped[section], columns=["Email / UID"]),
                    width='stretch',
                    hide_index=True,
                )

        with col1:
            sel_date_label = st.selectbox(
                "Date",
                options=_DATE_LABELS,
                index=_DEFAULT_DATE_IDX,
                key=f"append_date_{rule_prefix}_{section}",
                label_visibility="collapsed",
            )
            sel_date = _DATE_VALUES[_DATE_LABELS.index(sel_date_label)]

        with col2:
            sel_start_label = st.selectbox(
                "Start",
                options=_TIME_LABELS,
                index=_DEFAULT_START_IDX,
                key=f"append_start_{rule_prefix}_{section}",
                label_visibility="collapsed",
            )
            sel_start = _TIME_VALUES[_TIME_LABELS.index(sel_start_label)]

        with col3:
            sel_end_label = st.selectbox(
                "End",
                options=_TIME_LABELS,
                index=_DEFAULT_END_IDX,
                key=f"append_end_{rule_prefix}_{section}",
                label_visibility="collapsed",
            )
            sel_end = _TIME_VALUES[_TIME_LABELS.index(sel_end_label)]

        with col4:
            st.markdown(
                f"<br><code>{get_tz_label(selected_tz_name, sel_date)}</code>",
                unsafe_allow_html=True,
            )

        append_slot_configs[section] = {
            "date": sel_date,
            "start": sel_start,
            "end": sel_end,
        }

    generated_allow_access = build_allow_access(
        append_sections,
        append_grouped,
        append_slot_configs,
    )
    generated_session_rules = [
        r for r in generated_allow_access if _is_session_allow_access_rule(r)
    ]

    import_mode = st.selectbox(
        "Import behavior",
        options=["Merge by Session Window", "Append as New Sessions"],
        key=f"import_mode_{rule_prefix}",
        help=(
            "Merge: combine UIDs into matching sessions (same comment + window). "
            "Append: always create new session blocks."
        ),
    )

    with st.expander("Import preview"):
        st.markdown(_render_schedule_table(generated_session_rules, selected_tz_name))

    if st.button("Apply Import to Editable Sessions"):
        merged, next_seq = _merge_imported_rules(
            st.session_state.editable_rules,
            generated_session_rules,
            merge_mode=import_mode,
            rule_prefix=rule_prefix,
            next_rule_seq=int(st.session_state.next_rule_seq),
        )
        st.session_state.editable_rules = merged
        st.session_state.next_rule_seq = next_seq
        st.success("Imported sessions have been applied to the editable schedule.")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Step 5 — Review Diff and Submit Pull Request
# ---------------------------------------------------------------------------

st.header("Step 5 — Review and Submit Pull Request")

final_editor_rules: list[dict] = st.session_state.editable_rules

validation_errors: list[str] = []
if not final_editor_rules:
    validation_errors.append("At least one editable session is required.")

for idx, rule in enumerate(final_editor_rules, start=1):
    start_dt = datetime.datetime.combine(rule["start_date"], rule["start_time"])
    end_dt = datetime.datetime.combine(rule["end_date"], rule["end_time"])
    if start_dt >= end_dt:
        validation_errors.append(
            f"Session #{idx}: Start must be earlier than End."
        )
    if not rule.get("uids"):
        validation_errors.append(f"Session #{idx}: UID list is empty.")

for msg in validation_errors:
    st.error(msg)

final_allow_access = [
    _editor_rule_to_allow_access_rule(r) for r in final_editor_rules
] + copy.deepcopy(st.session_state.passthrough_allow_access)

diff_summary = _calculate_access_diff(
    st.session_state.original_editor_rules,
    final_editor_rules,
)

pr_body_preview = build_pr_body_with_diff(
    selected_assessment,
    diff_summary,
    final_allow_access,
    selected_tz_name,
)

with st.expander("Pull Request preview", expanded=True):
    st.markdown(pr_body_preview)

if st.button(
    "Generate and Create Pull Request",
    type="primary",
    width='stretch',
    disabled=bool(validation_errors),
):
    # Detect stale source file before write (read-modify-write conflict guard)
    with st.spinner("Checking source file for conflicts..."):
        try:
            latest_fetched = repo.get_contents(json_path, ref=repo.default_branch)
            latest_file = latest_fetched[0] if isinstance(latest_fetched, list) else latest_fetched
        except GithubException as exc:
            st.error(
                f"Failed to re-check `{json_path}`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    if latest_file.sha != st.session_state.source_file_sha:
        st.error(
            "409 Conflict: `infoAssessment.json` changed on the remote branch "
            "after hydration. Please refresh/reload latest data before submitting."
        )
        st.stop()

    base_json = copy.deepcopy(st.session_state.base_json_snapshot)
    base_json["allowAccess"] = final_allow_access
    updated_content = json.dumps(base_json, indent=2, ensure_ascii=False)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    branch_name = f"update-access-{selected_assessment}-{timestamp}"

    with st.spinner(f"Creating branch `{branch_name}`..."):
        try:
            default_ref = repo.get_branch(repo.default_branch)
            repo.create_git_ref(
                ref=f"refs/heads/{branch_name}",
                sha=default_ref.commit.sha,
            )
        except GithubException as exc:
            st.error(
                f"Failed to create branch `{branch_name}`: "
                f"{exc.data.get('message', str(exc))}"
            )
            st.stop()

    commit_message = f"Scheduler sync: update allowAccess for {selected_assessment}"
    with st.spinner("Committing updated infoAssessment.json..."):
        try:
            repo.update_file(
                path=json_path,
                message=commit_message,
                content=updated_content,
                sha=latest_file.sha,
                branch=branch_name,
            )
        except GithubException as exc:
            if exc.status == 409:
                st.error(
                    "409 Conflict: remote file changed during write. "
                    "Please reload latest data and retry."
                )
            else:
                st.error(
                    f"Failed to commit to branch `{branch_name}`: "
                    f"{exc.data.get('message', str(exc))}"
                )
            st.stop()

    pr_title = f"Scheduler sync: Update access rules for {selected_assessment}"
    pr_body = build_pr_body_with_diff(
        selected_assessment,
        diff_summary,
        final_allow_access,
        selected_tz_name,
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
            if exc.status == 409:
                st.error(
                    "409 Conflict while creating PR. The remote branch changed; "
                    "please refresh and retry."
                )
            else:
                st.error(
                    f"Failed to create Pull Request: "
                    f"{exc.data.get('message', str(exc))}"
                )
            st.stop()

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
            "editable_sessions": len(final_editor_rules),
            "added_lines": len(diff_summary.get("added", [])),
            "modified_lines": len(diff_summary.get("modified", [])),
            "deleted_lines": len(diff_summary.get("deleted", [])),
        },
    )

    with st.expander("Committed infoAssessment.json — full preview", expanded=True):
        st.code(updated_content, language="json")

