"""
TLC Live CCTV -- Real-Time Exam Proctoring Dashboard
=====================================================
Maps live PrairieLearn session data onto a physical computer-lab seat grid so
proctors can monitor exam progress and catch anomalies at a glance.

This module is designed as a standalone Streamlit page (placed in ``pages/``).
All lab topology, roster, and event-log data are currently provided by mock
generators so the page can be fully exercised without a live PrairieLearn
instance.

Colour semantics for each seat card:
  - Grey   : vacant (no active session on that IP)
  - Green  : in-progress and IP matches the physical seat
  - Blue   : submitted
  - Red    : anomaly (UID not on roster, or duplicate IP usage)
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import random
from dataclasses import dataclass, field
from typing import Any

import streamlit as st
import streamlit.components.v1 as st_html

from pl_api_client import (
    fetch_assessments,
    fetch_live_exam_status,
    fetch_live_data_from_config,
    SessionRecord,
)
from audit_log import audit

# ============================================================================
# Page config (must be the first Streamlit command)
# ============================================================================

st.set_page_config(
    page_title="TLC Live CCTV",
    page_icon=None,
    layout="wide",
)

# ============================================================================
# Constants
# ============================================================================

# CIDR blocks that define the physical lab's IP pool.
LAB_CIDRS: list[str] = [
    "169.237.128.160/27",
    "169.237.128.192/28",
    "169.237.128.208/29",
]

# Grid dimensions for the 2-D seat map.
GRID_COLS: int = 10

# Row labels: A, B, C, ...
_ROW_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ============================================================================
# Data Models
# ============================================================================

@dataclass(frozen=True)
class Seat:
    """One physical workstation in the lab."""
    label: str          # e.g. "A1"
    ip: str             # e.g. "169.237.128.161"


@dataclass
class SessionEvent:
    """A single live-session record from PrairieLearn."""
    uid: str
    current_ip: str
    status: str                     # not_started | in_progress | submitted
    last_active: datetime.datetime


@dataclass
class Alert:
    """A proctor-facing alert entry."""
    severity: str       # "critical" | "warning" | "info"
    message: str
    timestamp: datetime.datetime


# ============================================================================
# Lab Topology Builder
# ============================================================================

def build_lab_topology(cidrs: list[str]) -> list[Seat]:
    """
    Expand CIDR blocks into usable host addresses and assign sequential
    seat labels (A1, A2, ... B1, B2, ...).
    """
    hosts: list[str] = []
    for cidr in cidrs:
        net = ipaddress.ip_network(cidr, strict=False)
        hosts.extend(str(h) for h in net.hosts())

    seats: list[Seat] = []
    for idx, ip in enumerate(hosts):
        row_letter = _ROW_LABELS[idx // GRID_COLS]
        col_number = (idx % GRID_COLS) + 1
        seats.append(Seat(label=f"{row_letter}{col_number}", ip=ip))
    return seats


def build_ip_to_seat_map(seats: list[Seat]) -> dict[str, Seat]:
    """Create a fast lookup from IP address to Seat."""
    return {s.ip: s for s in seats}


def _api_records_to_events(records: list[SessionRecord]) -> list[SessionEvent]:
    """Convert pl_api_client SessionRecord dicts into SessionEvent objects."""
    now = datetime.datetime.now()
    events: list[SessionEvent] = []
    for rec in records:
        ip = rec.get("ip") or "0.0.0.0"
        status = rec.get("status", "not_started")
        uid = rec.get("uid", "unknown")
        last_active_str = rec.get("last_active_time")

        if last_active_str:
            try:
                t = datetime.datetime.strptime(last_active_str, "%H:%M:%S")
                last_active = now.replace(
                    hour=t.hour, minute=t.minute, second=t.second, microsecond=0,
                )
            except ValueError:
                last_active = now
        else:
            last_active = now

        if status == "not_started":
            continue

        events.append(SessionEvent(
            uid=uid,
            current_ip=ip,
            status=status,
            last_active=last_active,
        ))
    return events


# ============================================================================
# Mock Data Generators
# ============================================================================

_MOCK_ROSTER: list[str] = [
    "drodrigue@ucdavis.edu",  "ulisanchez@ucdavis.edu",
    "ejsanders@ucdavis.edu",  "ysmzhang@ucdavis.edu",
    "hchxu@ucdavis.edu",      "drgoswami@ucdavis.edu",
    "jgguan@ucdavis.edu",     "kadoni@ucdavis.edu",
    "ejtorres@ucdavis.edu",   "mjparsons@ucdavis.edu",
    "nicgrant@ucdavis.edu",   "srophael@ucdavis.edu",
    "mrdriguez@ucdavis.edu",  "ababuhara@ucdavis.edu",
    "tjlau@ucdavis.edu",      "maemar@ucdavis.edu",
    "viyengar@ucdavis.edu",   "cynngo@ucdavis.edu",
    "sanrahimi@ucdavis.edu",  "jsmith@ucdavis.edu",
    "azhang@ucdavis.edu",     "bpatel@ucdavis.edu",
    "cjohnson@ucdavis.edu",   "dlee@ucdavis.edu",
    "ewilson@ucdavis.edu",    "fgarcia@ucdavis.edu",
    "gmartinez@ucdavis.edu",  "hrobinson@ucdavis.edu",
    "iclark@ucdavis.edu",     "jrodriguez@ucdavis.edu",
]


def generate_mock_events(
    seats: list[Seat],
    roster: list[str],
    *,
    seed: int | None = None,
) -> list[SessionEvent]:
    """
    Produce a deterministic-but-realistic set of live session events.

    Mix includes:
      - Normal in-progress sessions seated at correct IPs
      - Submitted sessions
      - Not-started students (absent from event stream)
      - 1 off-site login anomaly (IP outside lab)
      - 1 UID-not-on-roster anomaly
    """
    rng = random.Random(seed)
    now = datetime.datetime.now()
    events: list[SessionEvent] = []

    # Shuffle seats for random assignment
    available_seats = list(seats)
    rng.shuffle(available_seats)

    # Assign most roster students to seats
    seated_count = min(len(roster) - 2, len(available_seats))
    for i in range(seated_count):
        uid = roster[i]
        seat = available_seats[i]
        minutes_ago = rng.randint(1, 40)

        if i < seated_count - 4:
            status = "in_progress"
        elif i < seated_count - 1:
            status = "submitted"
        else:
            status = "not_started"

        events.append(SessionEvent(
            uid=uid,
            current_ip=seat.ip,
            status=status,
            last_active=now - datetime.timedelta(minutes=minutes_ago),
        ))

    # --- Anomaly 1: off-site login (roster student on a non-lab IP) ---
    offsite_uid = roster[seated_count]
    events.append(SessionEvent(
        uid=offsite_uid,
        current_ip="73.158.42.19",      # residential ISP address
        status="in_progress",
        last_active=now - datetime.timedelta(minutes=3),
    ))

    # --- Anomaly 2: unknown UID on a lab IP (seat squatter) ---
    squatter_seat = available_seats[seated_count] if seated_count < len(available_seats) else available_seats[-1]
    events.append(SessionEvent(
        uid="unknown_intruder@gmail.com",
        current_ip=squatter_seat.ip,
        status="in_progress",
        last_active=now - datetime.timedelta(minutes=1),
    ))

    return events


# ============================================================================
# Alert / Anomaly Engine
# ============================================================================

def detect_anomalies(
    events: list[SessionEvent],
    roster_set: set[str],
    ip_seat_map: dict[str, Seat],
) -> list[Alert]:
    """
    Scan the live event stream and return a list of alerts ordered by
    severity (critical first).
    """
    now = datetime.datetime.now()
    alerts: list[Alert] = []
    active_statuses = {"in_progress", "submitted"}

    # Track IPs used by in-progress sessions for duplicate detection
    ip_usage: dict[str, list[str]] = {}
    for ev in events:
        if ev.status == "in_progress":
            ip_usage.setdefault(ev.current_ip, []).append(ev.uid)

    for ev in events:
        # Rule 1: UID not on roster but has an active session in the lab
        if ev.uid not in roster_set and ev.status in active_statuses:
            alerts.append(Alert(
                severity="critical",
                message=(
                    f"UNAUTHORIZED: Student [{ev.uid}] is not on the "
                    f"exam roster but is active on IP {ev.current_ip}"
                ),
                timestamp=now,
            ))

        # Rule 2: Roster student logged in from outside the lab network
        if (
            ev.uid in roster_set
            and ev.status in active_statuses
            and ev.current_ip not in ip_seat_map
        ):
            alerts.append(Alert(
                severity="critical",
                message=(
                    f"OFF-SITE LOGIN: Student [{ev.uid}] is taking the "
                    f"exam from a non-lab IP ({ev.current_ip})"
                ),
                timestamp=now,
            ))

    # Rule 3: Multiple UIDs sharing the same lab IP
    for ip, uids in ip_usage.items():
        if len(uids) > 1 and ip in ip_seat_map:
            seat_label = ip_seat_map[ip].label
            uid_list = ", ".join(uids)
            alerts.append(Alert(
                severity="critical",
                message=(
                    f"DUPLICATE IP: Seat {seat_label} ({ip}) is shared "
                    f"by {len(uids)} sessions: [{uid_list}]"
                ),
                timestamp=now,
            ))

    # Sort: critical first, then warning, then info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a.severity, 9))
    return alerts


# ============================================================================
# Seat Status Resolver
# ============================================================================

_STATUS_VACANT     = "vacant"
_STATUS_NORMAL     = "normal"
_STATUS_SUBMITTED  = "submitted"
_STATUS_ANOMALY    = "anomaly"


def resolve_seat_states(
    seats: list[Seat],
    events: list[SessionEvent],
    roster_set: set[str],
) -> dict[str, dict[str, Any]]:
    """
    For every physical seat, determine its display state and the occupant info.

    Returns a dict keyed by seat label:
        {
            "A1": {
                "state": "normal" | "submitted" | "anomaly" | "vacant",
                "uid": str | None,
                "ip": str,
                "last_active": datetime | None,
                "tooltip": str,
            },
            ...
        }
    """
    # Index events by IP (only in-progress or submitted — ignore not_started)
    ip_events: dict[str, list[SessionEvent]] = {}
    for ev in events:
        if ev.status in ("in_progress", "submitted"):
            ip_events.setdefault(ev.current_ip, []).append(ev)

    result: dict[str, dict[str, Any]] = {}
    for seat in seats:
        evs = ip_events.get(seat.ip, [])
        if not evs:
            result[seat.label] = {
                "state": _STATUS_VACANT,
                "uid": None,
                "ip": seat.ip,
                "last_active": None,
                "tooltip": f"{seat.label} | {seat.ip} | Vacant",
            }
            continue

        # If multiple sessions on one IP, mark anomaly
        if len(evs) > 1:
            uids = ", ".join(e.uid for e in evs)
            result[seat.label] = {
                "state": _STATUS_ANOMALY,
                "uid": uids,
                "ip": seat.ip,
                "last_active": evs[0].last_active,
                "tooltip": f"{seat.label} | {seat.ip} | CONFLICT: {uids}",
            }
            continue

        ev = evs[0]

        # UID not on roster → anomaly
        if ev.uid not in roster_set:
            result[seat.label] = {
                "state": _STATUS_ANOMALY,
                "uid": ev.uid,
                "ip": seat.ip,
                "last_active": ev.last_active,
                "tooltip": (
                    f"{seat.label} | {seat.ip} | "
                    f"UNAUTHORIZED: {ev.uid}"
                ),
            }
            continue

        if ev.status == "submitted":
            state = _STATUS_SUBMITTED
        else:
            state = _STATUS_NORMAL

        active_str = ev.last_active.strftime("%H:%M:%S")
        result[seat.label] = {
            "state": state,
            "uid": ev.uid,
            "ip": seat.ip,
            "last_active": ev.last_active,
            "tooltip": (
                f"{seat.label} | {seat.ip} | "
                f"{ev.uid} | {ev.status} | Last: {active_str}"
            ),
        }

    return result


# ============================================================================
# HTML / CSS Renderer for the 2-D Seat Map
# ============================================================================

_SEAT_CSS = """
<style>
.cctv-grid {
    display: grid;
    grid-template-columns: repeat(""" + str(GRID_COLS) + """, 1fr);
    gap: 6px;
    padding: 8px;
}
.cctv-seat {
    position: relative;
    border-radius: 6px;
    padding: 8px 4px;
    text-align: center;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 12px;
    line-height: 1.35;
    cursor: default;
    transition: transform 0.12s;
    border: 1px solid rgba(0,0,0,0.06);
    min-height: 56px;
    display: flex;
    flex-direction: column;
    justify-content: center;
}
.cctv-seat:hover {
    transform: scale(1.06);
    z-index: 2;
    box-shadow: 0 4px 14px rgba(0,0,0,0.25);
}
.seat-label {
    font-weight: 700;
    font-size: 13px;
}
.seat-uid {
    font-size: 10px;
    opacity: 0.85;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
}

/* State colours */
.state-vacant    { background: #e8e8e8; color: #888; }
.state-normal    { background: #27ae60; color: #fff; }
.state-submitted { background: #2980b9; color: #fff; }
.state-anomaly   {
    background: #e74c3c;
    color: #fff;
    animation: pulse-red 0.8s ease-in-out infinite alternate;
}
@keyframes pulse-red {
    from { background: #e74c3c; }
    to   { background: #c0392b; box-shadow: 0 0 12px #e74c3c; }
}

/* Tooltip */
.cctv-seat .seat-tip {
    visibility: hidden;
    position: absolute;
    bottom: 110%;
    left: 50%;
    transform: translateX(-50%);
    background: #1e1e2f;
    color: #f0f0f0;
    font-size: 11px;
    padding: 6px 10px;
    border-radius: 5px;
    white-space: nowrap;
    z-index: 10;
    pointer-events: none;
    box-shadow: 0 2px 10px rgba(0,0,0,0.4);
}
.cctv-seat:hover .seat-tip {
    visibility: visible;
}

/* Row separator labels */
.cctv-row-label {
    grid-column: 1 / -1;
    font-weight: 700;
    font-size: 13px;
    color: #555;
    padding: 4px 0 0 4px;
    border-bottom: 1px solid #ddd;
    margin-top: 4px;
}
</style>
"""


def _uid_display(uid: str | None) -> str:
    """Shorten a UID for the seat card (prefix before '@')."""
    if not uid:
        return "---"
    parts = uid.split(",")
    if len(parts) > 1:
        return f"{len(parts)} UIDs"
    return uid.split("@")[0] if "@" in uid else uid[:12]


def render_seat_map_html(
    seats: list[Seat],
    seat_states: dict[str, dict[str, Any]],
) -> str:
    """
    Build a complete HTML string for the 2-D lab map with colour-coded seats,
    hover tooltips, and row separators.
    """
    cards: list[str] = []
    current_row = ""

    for seat in seats:
        row_letter = seat.label[0]
        if row_letter != current_row:
            current_row = row_letter
            cards.append(
                f'<div class="cctv-row-label">Row {row_letter}</div>'
            )

        info = seat_states.get(seat.label, {
            "state": _STATUS_VACANT,
            "uid": None,
            "tooltip": f"{seat.label} | {seat.ip} | N/A",
        })
        state_cls = f"state-{info['state']}"
        uid_short = _uid_display(info.get("uid"))
        tooltip = info.get("tooltip", "")

        cards.append(
            f'<div class="cctv-seat {state_cls}" title="">'
            f'  <span class="seat-tip">{tooltip}</span>'
            f'  <span class="seat-label">{seat.label}</span>'
            f'  <span class="seat-uid">{uid_short}</span>'
            f'</div>'
        )

    grid_html = "\n".join(cards)
    return f"{_SEAT_CSS}\n<div class='cctv-grid'>\n{grid_html}\n</div>"


# ============================================================================
# Streamlit Page Layout
# ============================================================================

def render_kpi_bar(
    roster_size: int,
    present: int,
    submitted: int,
    anomaly_count: int,
) -> None:
    """Render the top KPI metric row."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Expected", roster_size)
    c2.metric("Present", present, delta=f"{present - roster_size}")
    c3.metric("Submitted", submitted)
    c4.metric("Anomalies", anomaly_count, delta_color="inverse")


def render_alert_feed(alerts: list[Alert]) -> None:
    """Render the right-hand audit / alert feed."""
    if not alerts:
        st.success("No anomalies detected. All sessions are clean.")
        return

    for alert in alerts:
        ts = alert.timestamp.strftime("%H:%M:%S")
        if alert.severity == "critical":
            st.error(f"**{ts}** -- {alert.message}")
        elif alert.severity == "warning":
            st.warning(f"**{ts}** -- {alert.message}")
        else:
            st.info(f"**{ts}** -- {alert.message}")


def render_event_log(events: list[SessionEvent]) -> None:
    """Render the scrollable raw event log table."""
    rows = []
    for ev in sorted(events, key=lambda e: e.last_active, reverse=True):
        rows.append({
            "UID": ev.uid,
            "IP": ev.current_ip,
            "Status": ev.status,
            "Last Active": ev.last_active.strftime("%H:%M:%S"),
        })
    st.dataframe(rows, width='stretch', hide_index=True, height=320)


# ============================================================================
# Main entry point
# ============================================================================

def main() -> None:
    st.title("TLC Live CCTV -- Exam Proctoring Dashboard")
    st.caption(
        "Real-time holographic view of the computer lab. "
        "Seat colours indicate student status; red seats require immediate attention."
    )

    # ------------------------------------------------------------------
    # Build static topology
    # ------------------------------------------------------------------
    seats = build_lab_topology(LAB_CIDRS)
    ip_seat_map = build_ip_to_seat_map(seats)
    roster = list(_MOCK_ROSTER)
    roster_set = set(roster)

    # ------------------------------------------------------------------
    # Sidebar controls
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("CCTV Controls")
        data_source = st.radio(
            "Data Source",
            options=["Mock", "Manual", "Env / Secrets"],
            index=0,
            help=(
                "Mock: deterministic demo data.  "
                "Manual: enter PL token and pick course/assessment.  "
                "Env / Secrets: read PL_API_TOKEN etc. from env vars or "
                ".streamlit/secrets.toml."
            ),
            horizontal=True,
        )
        use_mock = data_source == "Mock"
        use_manual = data_source == "Manual"
        use_env = data_source == "Env / Secrets"

        # -- Mock controls --
        if use_mock:
            mock_seed = st.number_input(
                "Random seed (mock data)",
                min_value=0,
                max_value=9999,
                value=42,
                step=1,
                help="Change this to simulate a different exam session snapshot.",
            )

        # -- Manual PL auth controls --
        pl_token: str = ""
        pl_base_url: str = "https://us.prairielearn.com"
        selected_ci_id: str = ""
        selected_a_id: str = ""

        if use_manual:
            st.subheader("PrairieLearn Connection")
            pl_base_url = st.text_input(
                "PL Server URL",
                value="https://us.prairielearn.com",
                help="Base URL of the PrairieLearn server (no trailing slash).",
            ).rstrip("/")
            pl_token = st.text_input(
                "API Token",
                type="password",
                help="Personal Access Token for PrairieLearn.",
            ).strip()

            if pl_token:
                # --- Course Instance ID: manual entry (no list-all endpoint in PL API) ---
                selected_ci_id = st.text_input(
                    "Course Instance ID",
                    help=(
                        "Find it in your PrairieLearn URL: "
                        "...prairielearn.com/pl/course_instance/{ID}/..."
                    ),
                ).strip()

                if selected_ci_id:
                    # --- Assessment dropdown ---
                    a_list = fetch_assessments(
                        pl_token, pl_base_url, selected_ci_id,
                    )
                    if a_list:
                        a_options: dict[str, str] = {}
                        for a in a_list:
                            a_id = str(
                                a.get("assessment_id")
                                or a.get("id", "")
                            )
                            a_label = (
                                a.get("assessment_name")
                                or a.get("tid")
                                or a.get("title")
                                or a_id
                            )
                            a_type = a.get("type", "")
                            display = f"{a_label}  [{a_type}]" if a_type else a_label
                            a_options[display] = a_id

                        chosen_a_label = st.selectbox(
                            "Assessment",
                            options=list(a_options.keys()),
                            help="Select the exam / assessment to monitor.",
                        )
                        selected_a_id = a_options.get(chosen_a_label, "")
                    else:
                        st.warning("No assessments found for this course instance.")
                else:
                    st.info("Enter your Course Instance ID to load assessments.")
            else:
                st.info("Enter your PrairieLearn API token to continue.")

        # -- Env / Secrets info --
        if use_env:
            st.info(
                "Reading config from secrets / env vars: "
                "PL_API_TOKEN, PL_BASE_URL, COURSE_INSTANCE_ID, ASSESSMENT_ID"
            )

        st.divider()
        auto_refresh = st.toggle("Auto-refresh (5 s)", value=False)
        if auto_refresh:
            st.caption("Page will re-run every 5 seconds to poll for updates.")

        st.divider()
        st.markdown(
            f"**Lab capacity:** {len(seats)} seats  \n"
            f"**Roster size:** {len(roster)} students  \n"
            f"**IP ranges:** {', '.join(LAB_CIDRS)}"
        )

    # ------------------------------------------------------------------
    # Fetch or generate session data
    # ------------------------------------------------------------------
    if use_mock:
        events = generate_mock_events(seats, roster, seed=int(mock_seed))
    elif use_manual:
        if pl_token and selected_ci_id and selected_a_id:
            # Log CCTV access (first load only per session key)
            _access_key = f"cctv_manual_{selected_ci_id}_{selected_a_id}"
            if _access_key not in st.session_state:
                st.session_state[_access_key] = True
                audit(
                    "cctv_access",
                    detail=f"Manual mode — CI={selected_ci_id}, A={selected_a_id}",
                    pat=pl_token,
                    meta={"mode": "manual", "base_url": pl_base_url},
                )
            api_records = fetch_live_exam_status(
                pl_token, pl_base_url, selected_ci_id, selected_a_id,
            )
            if api_records:
                events = _api_records_to_events(api_records)
                for rec in api_records:
                    uid = rec.get("uid", "")
                    if uid:
                        roster_set.add(uid)
            else:
                events = []
                st.warning("No data returned from PrairieLearn API. Showing empty map.")
        else:
            events = []
            st.info("Complete the PrairieLearn connection in the sidebar to load live data.")
    else:
        # Env / Secrets mode
        _env_key = "cctv_env_accessed"
        if _env_key not in st.session_state:
            st.session_state[_env_key] = True
            audit(
                "cctv_access",
                detail="Env / Secrets mode",
                meta={"mode": "env"},
            )
        api_records = fetch_live_data_from_config()
        if api_records:
            events = _api_records_to_events(api_records)
            for rec in api_records:
                uid = rec.get("uid", "")
                if uid:
                    roster_set.add(uid)
        else:
            events = []
            st.warning("No data returned from PrairieLearn API. Showing empty map.")
    seat_states = resolve_seat_states(seats, events, roster_set)
    alerts = detect_anomalies(events, roster_set, ip_seat_map)

    # Audit-log alerts and event log for non-Mock modes
    if not use_mock and alerts:
        audit(
            "cctv_alert",
            detail=f"{len(alerts)} anomal(ies) detected",
            meta={
                "count": len(alerts),
                "summaries": [
                    {"severity": a.severity, "message": a.message}
                    for a in alerts[:20]
                ],
            },
        )
    if not use_mock and events:
        audit(
            "cctv_event_log",
            detail=f"{len(events)} session event(s)",
            meta={
                "total": len(events),
                "in_progress": sum(1 for e in events if e.status == "in_progress"),
                "submitted": sum(1 for e in events if e.status == "submitted"),
            },
        )

    # ------------------------------------------------------------------
    # Compute KPI values
    # ------------------------------------------------------------------
    active_events = [e for e in events if e.status in ("in_progress", "submitted")]
    present_uids = {e.uid for e in active_events if e.uid in roster_set}
    submitted_count = sum(
        1 for e in events if e.status == "submitted" and e.uid in roster_set
    )

    render_kpi_bar(
        roster_size=len(roster),
        present=len(present_uids),
        submitted=submitted_count,
        anomaly_count=len(alerts),
    )

    st.divider()

    # ------------------------------------------------------------------
    # Two-column layout: seat map (left) + alerts (right)
    # ------------------------------------------------------------------
    col_map, col_feed = st.columns([3, 1])

    with col_map:
        st.subheader("Physical Seat Map")
        map_html = render_seat_map_html(seats, seat_states)
        st_html.html(map_html, height=620, scrolling=True)

        # Legend
        st.markdown(
            '<div style="display:flex;gap:18px;font-size:13px;padding:4px 8px;">'
            '<span style="color:#888;">&#9632; Vacant</span>'
            '<span style="color:#27ae60;">&#9632; In Progress</span>'
            '<span style="color:#2980b9;">&#9632; Submitted</span>'
            '<span style="color:#e74c3c;">&#9632; Anomaly</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    with col_feed:
        st.subheader("Alert Feed")
        render_alert_feed(alerts)

        st.divider()
        st.subheader("Event Log")
        render_event_log(events)

    # ------------------------------------------------------------------
    # Auto-refresh via st.rerun (Streamlit 1.27+)
    # ------------------------------------------------------------------
    if auto_refresh:
        import time
        time.sleep(5)
        st.rerun()


main()
