# PrairieLearn Course Automation Tools

This repository collects course-operation tools used around PrairieLearn exams:

- **Roster Bridge**: a standalone Chrome / Edge extension that converts Canvas rosters and optional SDC lists into PrairieLearn student-label CSV files locally.
- **PrairieLearn Exam Scheduler / Pull Request Scheduler**: a Streamlit app that generates `infoAssessment.json` access rules from a Canvas roster and opens a GitHub pull request.
- **LiveCCTV**: a Streamlit page showing monitored IP addresses and the full email addresses of students currently taking an exam. Cards do not represent physical seats.
- **PrairieLearn to Canvas sync pipeline**: a command-line script that transforms PrairieLearn grade exports into Canvas bulk grade updates.

CSV files are treated as temporary semester data. Do not rely on checked-in CSV files for future offerings; generate or upload fresh data each term.

## User Guide for Instructors and TAs

### Choose the right tool

| What you want to do | Tool | What you need |
| --- | --- | --- |
| Assign Canvas students to section and accommodation labels | **Roster Bridge extension** | Canvas roster CSV, optional SDC/additional-label CSV, and existing PL label names |
| Schedule assessments using modern label-based rules | **PrairieLearn Access page** | Labels with students assigned and course editing permissions |
| Generate legacy access rules from email lists and open a PR | **Streamlit Exam Scheduler** | Course GitHub repository URL, GitHub PAT, and student list |
| See who is taking an exam on each monitored IP | **Streamlit Live CCTV** | PrairieLearn API Token, Course Instance ID, and assessment |

The extension and Streamlit app are independent. The extension needs no server,
Streamlit installation, or token.

### 1. Install and use the browser extension

1. Obtain the repository's `chrome-extension` directory or extract the supplied extension ZIP.
2. Open `chrome://extensions` in Chrome or `edge://extensions` in Edge.
3. Enable **Developer mode**, choose **Load unpacked**, and select the directory
   that directly contains `manifest.json`.
4. Pin **PrairieLearn Roster Bridge** to the toolbar and click its icon.
   If your browser blocks CRX installation, use the unpacked method above.
5. Under **Canvas roster**, select your Canvas CSV. In **Match columns & labels**,
   choose the email column (for example, `SIS Login ID`) and section column.
   Student identifiers must be complete email addresses.
6. Map each section to an existing PL label. For example, map
   `ECS 032A A01 F2026` to `Section A`. Label names must match exactly.
7. Optionally select an **SDC / additional labels** CSV. It can contain email and
   group/multiplier columns, or use `uid,label1,label2,...`. Students are matched
   by email, never by a guessed name match. Multiple labels for the same student
   are combined into one row.
8. Check **Review the student list**. Rows with issues block export unless you
   explicitly accept exporting only valid rows. Verify the complete label sets,
   then choose **Copy CSV** or **Download PL CSV**.

Example output:

```csv
uid,label1,label2
alice@example.edu,Section A,SDC 1.5x
bob@example.edu,Section B,
```

Files are processed only in the extension tab. Reloading, closing the tab, or
clicking **Clear session** clears your work. Downloads and clipboard contents
are not deleted automatically. Filtering the preview does not restrict the export.

### 2. Apply the labels in PrairieLearn

1. Create the required labels in PL first, or define them in
   `infoCourseInstance.json` and sync the course.
2. Open **Students → Manage enrollments → Synchronize student list → CSV**.
3. Paste the generated CSV and click **Compare**.
4. Review label additions/removals and student invitations/removals. Deselect
   changes you do not want before applying them.

**The CSV replaces each student's complete label set.** Include any existing
labels they should keep. The extension cannot read current PL label assignments.
This screen also synchronizes enrollment: check whether students omitted from
your CSV are listed for removal.

For each quiz, use its **Access → Overrides → Students by label** controls to
choose a label and set the release date, due date, password, and time limit.
Save the changes in PL; this workflow does not require a GitHub PR from our tool.
For a 50-minute exam with 1.5x accommodations, set a 75-minute time limit and a
sufficiently late deadline. Naming a label `SDC 1.5x` does not extend time automatically.
Update assignments when students add/drop the course, change sections, or receive
new accommodations.

If the CSV option is missing, check that the course instance uses modern
publishing and the PL deployment includes
[CSV label synchronization #15909](https://github.com/PrairieLearn/PrairieLearn/pull/15909).
See the [extension guide](chrome-extension/README.md) for detailed file requirements.

### 3. Open the Streamlit app

- **Hosted app:** Open the URL provided by your administrator. You do not need
  Python installed on your own computer.
- **Local Windows installation:** Install Python (this project has been checked
  with Python 3.12), download the complete repository, and double-click
  `run_venv.bat`. The first run installs dependencies and needs internet access.
  Keep the terminal open and visit the URL it displays, usually `http://localhost:8501`.
- **macOS / Linux:** Run `bash run_venv.sh` from the repository directory.
- To stop a locally running app, press **Ctrl+C** in its terminal.

### 4. Schedule an exam with Exam Scheduler (legacy workflow)

**The current Scheduler removes `accessControl` from the target file and writes
legacy `allowAccess` rules.** It does not convert modern rules. Maintain assessments
that have migrated to label-based access in the PL Access page instead of using
this temporary legacy workflow.

1. Under **GitHub Authentication**, enter the course repository URL, such as
   `https://github.com/owner/course-repo`, and a GitHub PAT with access to that
   repository. The PAT needs **Contents: Read & Write** and
   **Pull Requests: Read & Write** permissions. Use the GitHub repository URL
   and GitHub token here, not the PrairieLearn URL or PrairieLearn API Token.
2. Click **Connect**, then select the **Term** and assessment. Existing editable
   legacy sessions will load.
3. Under **Edit Existing Access Rules**, adjust time windows, student lists,
   passwords, and limits. You can also delete or add session blocks.
4. Under **Append or Merge New Sessions**, upload a Canvas CSV and select its
   section/email columns, or use **Manual Entry** to enter email addresses.
5. In **Configure imported session windows**, choose Date and Start. End defaults
   to 50 minutes later and remains editable. Use 24-hour times such as `9:00` or
   `19:00`; windows crossing midnight end on the following date.
6. Choose whether to merge or append sessions, click
   **Apply Import to Editable Sessions**, and review the complete schedule and PR preview.
7. Click **Generate and Create Pull Request**. Creating a PR does not activate
   the schedule: review and merge it in GitHub, let PrairieLearn sync the course,
   and verify the resulting settings on the Access page.
8. Click **Disconnect** or **Clear All** when finished to clear the current app
   session. This does not delete credentials saved in your browser's password manager.

If the source file changed or you see a **409 Conflict**, record any edits you
want to retain before clicking **Reload Latest From Main**. It reloads the repository's
default branch; review and reapply your edits before submitting again.
The selected timezone is for display and PR documentation. Saved timestamps use
local wall-clock time, so match the course instance's timezone in PL.

### 5. Monitor an exam with Live CCTV

1. Start the Streamlit app and select **Live CCTV** in the sidebar. You do not
   need to connect to GitHub first.
2. Choose **Mock** to explore the dashboard with demo data. It does not show real students.
3. For a real exam, choose **Manual** and enter the **PL Server URL** (for example,
   `https://us.prairielearn.com`) and a **PrairieLearn API Token** with permission
   to view the course's student data.
4. Find **Course Instance ID** in the PL URL's `/course_instance/12345/` segment.
   Enter that number and select the exam from the **Assessment** list.
5. Choose the display timezone and optionally enable **Auto-refresh (5 s)**.
   API caching can make fresh data arrive less often than the page refresh interval.
6. **IP Overview** shows configured IP addresses and the full emails of students
   currently taking an exam. Cards do not correspond to physical seats.
   **No active exam** means no in-progress exam is shown for that IP; submitted
   students no longer occupy a card. Use **Alert Feed** and **Event Log** to
   investigate activity. An empty card alone is not evidence that a student is absent.

**Env / Secrets** uses server credentials configured by an administrator and
shares access to their data. Use **Manual** for individual credentials; deployment
administrators should reserve shared configuration for access-controlled environments.

### Troubleshooting

| What you see | What to do |
| --- | --- |
| Extension export buttons are disabled | Check column mappings, row issues, and the complete-label-set confirmation. If you loaded course JSON, resolve unknown label names too. |
| SDC file contains names but no emails | Add verified email addresses first. The extension does not guess identity from similar names. |
| PL reports an unknown label | Create it first or change the mapping to match an existing label exactly. |
| Extension files disappear after refreshing | Select the files again. Student lists are intentionally not saved between tab sessions. |
| Scheduler reports a permission error | Check the GitHub PAT, target repository, and required permissions. |
| Live CCTV shows no real data | Check the selected mode, PL token, course/assessment IDs, permissions, and whether exam records exist. |

## Developer / Deployment Reference

The sections below describe repository layout, local launch commands and configuration.

## Repository Structure

```text
.
├── app.py                         # Streamlit main app: exam scheduling + GitHub PR workflow
├── pages/
│   └── live_cctv.py               # Streamlit multipage page: LiveCCTV dashboard
├── pl_api_client.py               # PrairieLearn API helper used by LiveCCTV
├── sync_pipeline.py               # CLI: PrairieLearn grade export -> Canvas API payload
├── audit_log.py                   # Shared local audit logger
├── github_login.py                # Browser-autofill-compatible GitHub login component
├── components/github_login/       # Native login form assets
├── chrome-extension/              # Independent Chrome / Edge roster-to-label CSV extension
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

The scheduler currently always writes legacy `allowAccess` rules. If the source
contains `accessControl`, that field is removed; its modern defaults and overrides
are not converted. Configure all required sessions before submitting. The UI and
PR description identify this replacement. Other assessment fields are preserved.

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

In **Manual** mode, each visitor enters their own token and IDs. **Env / Secrets**
uses shared deployment configuration; restrict access to the deployment accordingly.
Keep `.streamlit/secrets.toml` out of version control.

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
- Token fields use password inputs with `autocomplete="current-password"` so
  the visitor's browser/password manager can offer to save and fill them.
  The app does not save credentials for autofill or prefill token fields.
  Active input and API client state remain session-local; Disconnect / Clear All
  clears that session's state, not the browser's saved passwords.
- Legacy `.pl_credentials.json` files are no longer read or written. After
  upgrading, remove the old file on the deployment host and revoke/rotate any
  PAT that may have been exposed. Restart the app to end pre-upgrade sessions.
- Live CCTV's explicit **Env / Secrets** mode uses deployment-wide credentials
  and makes their data available to visitors. Use it only in a trusted,
  access-controlled deployment; **Manual** mode uses each visitor's own token.
- `.pl_credentials.json`, `.streamlit/`, `logs/`, and CSV data are ignored.
- Generated `infoAssessment.json` files may contain student identifiers; review course and institution data policies before committing them.
