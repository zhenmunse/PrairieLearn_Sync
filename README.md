# PrairieLearn Course Automation Tools

A collection of Python utilities for automating administrative workflows in PrairieLearn-based university courses. This repository contains two independent tools: a Streamlit web application for generating assessment configuration files, and a command-line ETL pipeline for synchronizing grades from PrairieLearn to Canvas.

---

## Repository Structure

```
.
├── app.py                  # Streamlit GUI — infoAssessment.json generator
├── sync_pipeline.py        # CLI pipeline — PrairieLearn to Canvas grade sync
├── requirements.txt        # Python package dependencies
├── run_venv.bat / run_venv.sh       # Launch using an isolated virtual environment
├── run_system.bat / run_system.sh   # Launch using the system Python installation
├── example.json            # Reference infoAssessment.json template
├── export.csv              # Sample Canvas Roster CSV
└── Example/                # Sample PrairieLearn question structures
```

---

## Tool 1: infoAssessment.json Generator (app.py)

### Purpose

Generating PrairieLearn `infoAssessment.json` files with correct `allowAccess` rules requires manually mapping each course section to a time slot and a list of student UIDs. This process is error-prone when performed by hand. This tool provides a web-based graphical interface that automates the process from a Canvas Roster CSV export.

### How It Works

1. The user connects to the GitHub repository using a Personal Access Token.
2. The user selects the target Term and Assessment from cascading dropdowns populated from the repository.
3. The user uploads a Canvas Roster CSV and maps the section and email columns.
4. A start and end time (with timezone selection and automatic DST resolution) is configured independently for each detected section.
5. The tool commits the updated `infoAssessment.json` to a new branch and opens a Pull Request targeting the default branch.

The original JSON structure (metadata, `zones`, etc.) is preserved. Only the `allowAccess` array is replaced.

### Running the Application

Use one of the provided launch scripts, which handle dependency installation automatically:

```bat
:: Windows — isolated virtual environment (recommended)
run_venv.bat

:: Windows — system Python
run_system.bat
```

```bash
# macOS / Linux — isolated virtual environment (recommended)
chmod +x run_venv.sh && ./run_venv.sh

# macOS / Linux — system Python
chmod +x run_system.sh && ./run_system.sh
```

Alternatively, install dependencies manually and launch directly:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The application will be available at `http://localhost:8501` by default.

### Input Requirements

| Input | Format | Required |
|---|---|---|
| GitHub Repository URL | HTTPS URL | Yes |
| GitHub Personal Access Token | PAT with Contents and Pull Requests permissions | Yes |
| Student roster | CSV exported from Canvas Grades | Yes |

The Canvas Roster CSV must contain at least one column identifying the section and one column containing the student email or SIS Login ID. Column names are user-selectable via dropdown menus in the interface.

### Output Format

Each entry in the generated `allowAccess` array follows this schema:

```json
{
  "mode": "Exam",
  "startDate": "YYYY-MM-DDTHH:MM:SS",
  "endDate": "YYYY-MM-DDTHH:MM:SS",
  "uids": ["student1@ucdavis.edu", "student2@ucdavis.edu"]
}
```

Times are written as wall-clock local time in the selected timezone. The timezone and DST offset are recorded in the Pull Request description for reference.

---

## Tool 2: Grade Synchronization Pipeline (sync_pipeline.py)

### Purpose

This ETL (Extract, Transform, Load) pipeline automates the synchronization of grades from a PrairieLearn CSV export to a Canvas assignment via the Canvas Submissions Bulk Update API.

### How It Works

1. **Extract**: Reads a PrairieLearn `*_points_by_username.csv` export.
2. **Transform**: Cleans the dataset and converts it into the Canvas API batch payload format, mapping student usernames to SIS User IDs.
3. **Load**: Submits a bulk grade update via an HTTP POST request to the Canvas API.

### Prerequisites

- Python 3.8 or later
- A valid Canvas API token with submission write permissions

Store the API token as an environment variable. Do not hardcode credentials in the script or commit them to source control.

```bash
# Unix / macOS
export CANVAS_API_TOKEN="your_canvas_token_here"

# Windows (PowerShell)
$env:CANVAS_API_TOKEN = "your_canvas_token_here"
```

### Usage

```bash
python sync_pipeline.py --csv <path_to_csv> --course <canvas_course_id> --assignment <canvas_assignment_id>
```

### Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--csv` | Yes | Path to the PrairieLearn export CSV |
| `--course` | Yes | Target Canvas Course ID |
| `--assignment` | Yes | Target Canvas Assignment ID |
| `--domain` | No | Canvas instance base URL (default: `https://canvas.ucdavis.edu`) |
| `--commit` | No | Disable dry-run mode and execute the API write |

### Dry-Run Mode

By default, the pipeline runs in **dry-run mode**: it extracts and transforms the data and logs a payload preview, but does not send any request to the Canvas API. This is the recommended mode for validating data before committing a grade update.

To perform the actual grade upload, append the `--commit` flag:

```bash
python sync_pipeline.py \
    --csv ECS_32A_TEST26_Q2_points_by_username.csv \
    --course 12345 \
    --assignment 67890 \
    --commit
```

---

## Installation

The launch scripts (`run_venv.bat` / `run_venv.sh` and `run_system.bat` / `run_system.sh`) handle dependency installation automatically. For manual setup:

```bash
# Clone the repository
git clone <repository-url>
cd <repository-directory>

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate       # Unix / macOS
.venv\Scripts\activate          # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## Security Notes

- The GitHub Personal Access Token must be entered at runtime only. It is never stored to disk by this tool and must never be committed to version control.
- The Canvas API token for `sync_pipeline.py` must be supplied via the `CANVAS_API_TOKEN` environment variable. It must never be committed to version control.
- Generated `infoAssessment.json` files may contain student email addresses. Review your institution's data policy before committing such files to a shared repository. The `.gitignore` in this project excludes the generated output file by default.


