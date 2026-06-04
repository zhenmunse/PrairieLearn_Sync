# Repository Structure Notes

This repository is intended to carry forward between course offerings. Keep durable code, examples, and documentation in version control; keep semester-specific data out.

## Durable Code

- `app.py`: PrairieLearn exam scheduler and GitHub pull-request workflow.
- `pages/live_cctv.py`: LiveCCTV Streamlit page. It stays under `pages/` so Streamlit discovers it automatically.
- `pl_api_client.py`: PrairieLearn API access layer for LiveCCTV.
- `sync_pipeline.py`: PrairieLearn grade export to Canvas API pipeline.
- `audit_log.py`: Shared local JSONL audit logger.

## Examples

- `examples/infoAssessment.template.json`: Reference assessment configuration template.
- `examples/prairielearn-questions/`: Sample PrairieLearn question directories.

## Temporary or Local Files

Do not commit these:

- CSV files from Canvas or PrairieLearn exports.
- `.pl_credentials.json`
- `.streamlit/secrets.toml`
- `logs/audit.jsonl`
- `.venv/`, `__pycache__/`, and other runtime caches.

Use `temp-data/` for one-off CSVs during a semester. The directory is ignored and can be emptied whenever the course offering ends.

## Future Semester Checklist

1. Install dependencies from `requirements.txt`.
2. Configure Streamlit secrets or environment variables for PrairieLearn/Canvas.
3. Launch `streamlit run app.py` or use `run_venv`.
4. Upload fresh Canvas/PrairieLearn CSV exports; do not reuse old `temp-data/` files.
5. Review `LAB_CIDRS` in `pages/live_cctv.py` if the lab room or IP ranges change.
