"""
PrairieLearn Live Exam API Client
==================================
Fetches real-time assessment-instance and event-log data from PrairieLearn,
cleans it, and outputs a list of dicts that the CCTV dashboard can consume
directly.

Configuration is read from Streamlit secrets (``st.secrets``) or environment
variables as a fallback:

    PL_API_TOKEN          Personal Access Token for PrairieLearn
    PL_BASE_URL           Base URL of the PL server (default: https://us.prairielearn.com)
    COURSE_INSTANCE_ID    Numeric course-instance ID
    ASSESSMENT_ID         Numeric assessment ID

The main public entry point is ``fetch_live_exam_status()`` which returns a
list of ``SessionRecord`` dicts ready for the CCTV dashboard.  It is wrapped
with ``@st.cache_data(ttl=15)`` so the upstream API is polled at most once
every 15 seconds.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any, TypedDict

import requests
import streamlit as st

# ============================================================================
# Logging
# ============================================================================

logger = logging.getLogger(__name__)

# ============================================================================
# Data Contract
# ============================================================================


class SessionRecord(TypedDict):
    """Output schema consumed by the CCTV dashboard."""
    uid: str
    status: str                     # "not_started" | "in_progress" | "submitted"
    ip: str | None
    last_active_time: str | None    # "HH:MM:SS" formatted string


# ============================================================================
# Configuration Loader
# ============================================================================


def _get_config(key: str, default: str | None = None) -> str:
    """
    Read a configuration value in order of precedence:
      1. ``st.secrets[key]``        (recommended for deployed apps)
      2. ``os.environ[key]``        (fallback / CI)
      3. *default*                  (only if provided)
    Raises ``RuntimeError`` if the key is missing everywhere and no default
    was given.
    """
    # Streamlit secrets (toml-based)
    try:
        val = st.secrets[key]
        if val:
            return str(val)
    except (KeyError, FileNotFoundError):
        pass

    # Environment variable
    val = os.environ.get(key)
    if val:
        return val

    if default is not None:
        return default

    raise RuntimeError(
        f"Missing required configuration: '{key}'. "
        f"Set it in .streamlit/secrets.toml or as an environment variable."
    )


# ============================================================================
# Low-Level HTTP Helpers
# ============================================================================

_REQUEST_TIMEOUT: int = 12   # seconds per request


def _build_session(token: str) -> requests.Session:
    """Return a pre-configured requests.Session with auth headers."""
    session = requests.Session()
    session.headers.update({
        "Private-Token": token,
        "Accept": "application/json",
    })
    return session


def _safe_get(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """
    Perform a GET request with error handling.  Returns the parsed JSON on
    success, or ``None`` on any failure (network, 4xx/5xx, decode error).
    Errors are logged and surfaced via ``st.error`` so the page never crashes.
    """
    try:
        resp = session.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "N/A"
        logger.error("PL API HTTP %s for %s: %s", status, url, exc)
        st.error(
            f"PrairieLearn API returned HTTP {status}. "
            "Check your API token and IDs."
        )
    except requests.exceptions.ConnectionError:
        logger.error("Connection error reaching %s", url)
        st.error("Cannot reach the PrairieLearn server. Check your network.")
    except requests.exceptions.Timeout:
        logger.error("Request to %s timed out", url)
        st.error("PrairieLearn API request timed out. Retrying next cycle.")
    except requests.exceptions.RequestException as exc:
        logger.error("Unexpected request error: %s", exc)
        st.error(f"Unexpected API error: {exc}")
    except ValueError:
        logger.error("Failed to decode JSON from %s", url)
        st.error("Received invalid JSON from PrairieLearn API.")
    return None


# ============================================================================
# API Endpoints — Navigation (Assessments for a given Course Instance)
# ============================================================================

# Note: PrairieLearn has no public endpoint to list all course instances.
# Users must supply their Course Instance ID manually (visible in the PL URL:
# https://us.prairielearn.com/pl/course_instance/{ID}/instructor/assessments).


@st.cache_data(ttl=300, show_spinner="Loading assessments...")
def fetch_assessments(
    _token: str,
    base_url: str,
    course_instance_id: str,
) -> list[dict[str, Any]]:
    """
    GET /pl/api/v1/course_instances/{cid}/assessments

    Returns a list of assessment dicts for a specific course instance.
    Cached for 5 minutes.
    """
    session = _build_session(_token)
    url = f"{base_url}/pl/api/v1/course_instances/{course_instance_id}/assessments"
    data = _safe_get(session, url)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "assessments" in data:
        return data["assessments"]
    return []


# ============================================================================
# API Endpoints — Live Exam Data
# ============================================================================


def _fetch_assessment_instances(
    session: requests.Session,
    base_url: str,
    course_instance_id: str,
    assessment_id: str,
) -> list[dict[str, Any]]:
    """
    GET /api/v1/course_instances/{cid}/assessments/{aid}/assessment_instances

    Returns the raw list of assessment-instance objects, or an empty list on
    failure.
    """
    url = (
        f"{base_url}/pl/api/v1/course_instances/{course_instance_id}"
        f"/assessments/{assessment_id}/assessment_instances"
    )
    data = _safe_get(session, url)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "assessment_instances" in data:
        return data["assessment_instances"]
    if data is None:
        return []
    # Unexpected shape — return empty
    logger.warning("Unexpected response shape from assessment_instances endpoint")
    return []


def _fetch_instance_events(
    session: requests.Session,
    base_url: str,
    course_instance_id: str,
    assessment_instance_id: str | int,
) -> list[dict[str, Any]]:
    """
    GET /api/v1/course_instances/{cid}/assessment_instances/{aiid}/log

    Fetches the event log for a single assessment instance.  We only need the
    most recent entry for IP extraction, but the API returns the full log.
    """
    url = (
        f"{base_url}/pl/api/v1/course_instances/{course_instance_id}"
        f"/assessment_instances/{assessment_instance_id}/log"
    )
    data = _safe_get(session, url)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "log" in data:
        return data["log"]
    return []


# ============================================================================
# Data Transformation
# ============================================================================


def _determine_status(instance: dict[str, Any]) -> str:
    """Map a PL assessment-instance record to a dashboard status string."""
    is_open = instance.get("open", False)
    score = instance.get("score_perc") or instance.get("points")
    if is_open:
        return "in_progress"
    if score is not None:
        return "submitted"
    return "not_started"


def _extract_latest_ip(events: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """
    Walk the event log (newest-first or sorted by date) and return a
    (ip_address, last_active_time_str) tuple.
    """
    if not events:
        return None, None

    # Sort descending by timestamp to guarantee we pick the latest entry
    sorted_events = sorted(
        events,
        key=lambda e: (
            e.get("date")
            or e.get("event_date")
            or e.get("date_iso8601")
            or ""
        ),
        reverse=True,
    )

    for ev in sorted_events:
        # PrairieLearn may place IP on either top-level keys or under
        # client_fingerprint.ip_address.
        client_fp = ev.get("client_fingerprint")
        ip = (
            ev.get("ip")
            or ev.get("ip_address")
            or ev.get("client_ip")
            or (client_fp.get("ip_address") if isinstance(client_fp, dict) else None)
            or (client_fp.get("client_ip") if isinstance(client_fp, dict) else None)
        )
        date_str = (
            ev.get("date")
            or ev.get("event_date")
            or ev.get("date_iso8601")
            or ""
        )
        if ip:
            last_active = _format_time(date_str)
            return ip, last_active

    # Fallback: return the timestamp of the newest event without an IP
    date_str = (
        sorted_events[0].get("date")
        or sorted_events[0].get("event_date")
        or sorted_events[0].get("date_iso8601")
        or ""
    )
    return None, _format_time(date_str)


def _format_time(iso_str: str) -> str | None:
    """Parse an ISO-8601 datetime string and return 'HH:MM:SS', or None."""
    if not iso_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(iso_str, fmt)
            return dt.strftime("%H:%M:%S")
        except ValueError:
            continue
    # Last resort: try dateutil if available
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.isoparse(iso_str)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return None


# ============================================================================
# Main Public Function
# ============================================================================


@st.cache_data(ttl=15, show_spinner="Fetching live data from PrairieLearn...")
def fetch_live_exam_status(
    _token: str,
    base_url: str,
    course_instance_id: str,
    assessment_id: str,
) -> list[SessionRecord]:
    """
    Fetch assessment instances and their latest IPs, returning a cleaned list
    of ``SessionRecord`` dicts.

    Cached for 15 seconds to respect PrairieLearn API rate limits.  On any
    failure the function returns an empty list so the UI degrades gracefully.

    Parameters
    ----------
    _token : str
        PL API token.  Prefixed with ``_`` so Streamlit's cache hashing
        skips it (tokens should not appear in cache keys).
    base_url : str
        PrairieLearn server URL (no trailing slash).
    course_instance_id : str
        Numeric course-instance ID.
    assessment_id : str
        Numeric assessment ID.
    """
    session = _build_session(_token)

    # Step 1: Fetch all assessment instances
    instances = _fetch_assessment_instances(
        session, base_url, course_instance_id, assessment_id,
    )
    if not instances:
        return []

    # Build intermediate records keyed by instance ID
    records: list[dict[str, Any]] = []
    for inst in instances:
        uid = (
            inst.get("uid")
            or inst.get("user_uid")
            or inst.get("user", {}).get("uid", "unknown")
        )
        status = _determine_status(inst)
        instance_id = inst.get("assessment_instance_id") or inst.get("id")
        records.append({
            "uid": uid,
            "status": status,
            "instance_id": instance_id,
        })

    # Step 2: Fetch IPs for all started sessions (in-progress + submitted).
    # This keeps CCTV/export useful even after students submit.
    started_records = [
        r for r in records if r["status"] in ("in_progress", "submitted")
    ]

    # Concurrency guard: sequential requests to avoid hammering the API.
    # For large exams (>50 in-progress) we truncate to avoid timeouts.
    MAX_LOG_FETCHES = 80
    ip_map: dict[str, tuple[str | None, str | None]] = {}

    for rec in started_records[:MAX_LOG_FETCHES]:
        iid = rec["instance_id"]
        if iid is None:
            continue
        events = _fetch_instance_events(
            session, base_url, course_instance_id, str(iid),
        )
        ip, last_active = _extract_latest_ip(events)
        ip_map[rec["uid"]] = (ip, last_active)

    if len(started_records) > MAX_LOG_FETCHES:
        logger.warning(
            "Skipped log fetches for %d started instances (limit: %d)",
            len(started_records) - MAX_LOG_FETCHES,
            MAX_LOG_FETCHES,
        )

    # Step 3: Assemble final output
    output: list[SessionRecord] = []
    for rec in records:
        ip, last_active = ip_map.get(rec["uid"], (None, None))
        output.append(SessionRecord(
            uid=rec["uid"],
            status=rec["status"],
            ip=ip,
            last_active_time=last_active,
        ))

    return output


# ============================================================================
# Convenience: load config and fetch in one call (env-var / secrets mode)
# ============================================================================


def fetch_live_data_from_config() -> list[SessionRecord]:
    """
    Read API configuration from secrets / env vars and return live session
    records.  Returns an empty list if any config is missing or the API is
    unreachable.
    """
    try:
        token = _get_config("PL_API_TOKEN")
        base_url = _get_config("PL_BASE_URL", "https://us.prairielearn.com")
        course_instance_id = _get_config("COURSE_INSTANCE_ID")
        assessment_id = _get_config("ASSESSMENT_ID")
    except RuntimeError as exc:
        st.error(str(exc))
        return []

    return fetch_live_exam_status(token, base_url, course_instance_id, assessment_id)
