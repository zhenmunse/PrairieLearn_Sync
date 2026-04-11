"""
Audit Logger
=============
Append-only structured audit log.  Each entry is a single JSON line written
to ``logs/audit.jsonl``.  The file is created automatically on first write.

PAT values are masked: only the first 4 and last 4 characters are kept,
with a SHA-256 fingerprint so the same token can be correlated across
entries without exposing the secret.

Usage
-----
    from audit_log import audit

    audit("auth", detail="Connected to owner/repo", pat="ghp_xxxx...")
    audit("file_upload", detail="roster.csv", meta={"size_bytes": 12345})
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FILE = _LOG_DIR / "audit.jsonl"

_logger = logging.getLogger("audit")


def _mask_pat(pat: str) -> dict[str, str]:
    """Return a dict with a masked preview and a SHA-256 fingerprint."""
    if len(pat) <= 8:
        preview = "***"
    else:
        preview = f"{pat[:4]}...{pat[-4:]}"
    fingerprint = hashlib.sha256(pat.encode()).hexdigest()[:16]
    return {"preview": preview, "fingerprint": fingerprint}


def audit(
    event: str,
    *,
    detail: str = "",
    pat: str | None = None,
    meta: dict | None = None,
) -> None:
    """
    Append one audit record to the log file.

    Parameters
    ----------
    event : str
        Event type, e.g. ``"auth"``, ``"file_upload"``, ``"submission"``,
        ``"cctv_access"``, ``"cctv_alert"``, ``"cctv_event_log"``.
    detail : str
        Human-readable description.
    pat : str, optional
        If provided, a masked + fingerprinted version is stored.
    meta : dict, optional
        Arbitrary JSON-serialisable metadata.
    """
    record: dict = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": event,
    }
    if detail:
        record["detail"] = detail
    if pat:
        record["pat"] = _mask_pat(pat)
    if meta:
        record["meta"] = meta

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        _logger.warning("Failed to write audit log: %s", exc)
