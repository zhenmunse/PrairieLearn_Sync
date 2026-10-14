"""Minimal audit trail: UTC timestamp and allowlisted operation only.

Caller-provided detail, credentials and metadata are deliberately discarded.
No identity, token fragment/fingerprint, repository, exam, IP or payload is logged.
"""
import datetime
import json
import logging
from pathlib import Path

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "audit.jsonl"
_logger = logging.getLogger("audit")
_EVENTS = frozenset({"auth", "file_upload", "submission", "cctv_access", "cctv_export"})


def audit(event: str, *, detail: str = "", pat: str | None = None, meta: dict | None = None) -> None:
    """Keep only an allowlisted operation and timestamp, never caller data."""
    if event not in _EVENTS:
        return
    record = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "event": event}
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        _logger.warning("Audit record could not be written.")
