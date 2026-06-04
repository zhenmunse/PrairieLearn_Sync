# PrairieLearn Course Automation Tools

This repository collects course-operation tools used around PrairieLearn exams:

- **PrairieLearn Exam Scheduler / Pull Request Scheduler**: a Streamlit app that generates `infoAssessment.json` access rules from a Canvas roster and opens a GitHub pull request.
- **LiveCCTV**: a Streamlit page for monitoring live PrairieLearn exam sessions on a lab-seat map.
- **PrairieLearn to Canvas sync pipeline**: a command-line script that transforms PrairieLearn grade exports into Canvas bulk grade updates.

CSV files are treated as temporary semester data. Do not rely on checked-in CSV files for future offerings; generate or upload fresh data each term.

## Repository Structure

```text
.
├── app.py                         # Streamlit main app: exam scheduling + GitHub PR workflow
├── pages/
│   └── live_cctv.py               # Streamlit multipage page: LiveCCTV dashboard
├── pl_api_client.py               # PrairieLearn API helper used by LiveCCTV
├── sync_pipeline.py               # CLI: PrairieLearn grade export -> Canvas API payload
├── audit_log.py                   # Shared local audit logger
├── examples/
│   ├── infoAssessment.template.json
│   └── prairielearn-questions/    # Sample PrairieLearn question structures
├── docs/
│   └── STRUCTURE.md               # Maintenance notes for future semesters
├── requirements.txt               # Python dependencies
├── run_venv.bat / run_venv.sh     # Launch app with a local virtual environment
└── run_system.bat / run_system.sh # Launch app with system Python
```

Ignored local/runtime paths:

- `.venv/`, `__pycache__/`
- `.streamlit/`, `.pl_credentials.json`
- `logs/`
- `temp-data/`
- `*.csv`

## Tool 1: Exam Scheduler / PR Scheduler

### Purpose

Generating PrairieLearn `infoAssessment.json` files with correct `allowAccess` rules requires mapping each course section to a time slot and a list of student UIDs. This tool provides a web interface that automates the process from a Canvas roster CSV export.

### Workflow

1. Enter a GitHub repository URL and Personal Access Token.
2. Select the target term and assessment from the repository's `courseInstances/` tree.
3. Upload a Canvas roster CSV and map the section/email columns.
4. Configure start and end times for each section.
5. Commit the updated `infoAssessment.json` to a new branch and open a pull request.

The original JSON structure is preserved. Only the `allowAccess` array is replaced.

### Run

```bat
run_venv.bat
```

```bash
chmod +x run_venv.sh
./run_venv.sh
```

Manual launch:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app is usually available at `http://localhost:8501`.

## Tool 2: LiveCCTV

LiveCCTV is exposed as a Streamlit multipage page under `pages/live_cctv.py`. Launch the Streamlit app normally, then choose the LiveCCTV page in the sidebar.

Configuration is read from Streamlit secrets or environment variables:

```text
PL_API_TOKEN
PL_BASE_URL
COURSE_INSTANCE_ID
ASSESSMENT_ID
```

For deployed or shared use, prefer `.streamlit/secrets.toml` and keep that file out of version control.

## Tool 3: PrairieLearn to Canvas Sync

This command-line ETL pipeline reads a PrairieLearn `*_points_by_username.csv` export, converts it into the Canvas Submissions Bulk Update API payload, and optionally posts it to Canvas.

Set the Canvas token as an environment variable:

```bash
export CANVAS_API_TOKEN="your_canvas_token_here"
```

PowerShell:

```powershell
$env:CANVAS_API_TOKEN = "your_canvas_token_here"
```

Dry run:

```bash
python sync_pipeline.py --csv temp-data/ECS_32A_TEST26_Q2_points_by_username.csv --course 12345 --assignment 67890
```

Actual upload:

```bash
python sync_pipeline.py --csv temp-data/ECS_32A_TEST26_Q2_points_by_username.csv --course 12345 --assignment 67890 --commit
```

## Security Notes

- GitHub PATs and PrairieLearn/Canvas API tokens must not be committed.
- `.pl_credentials.json`, `.streamlit/`, `logs/`, and CSV data are ignored.
- Generated `infoAssessment.json` files may contain student identifiers; review course and institution data policies before committing them.
