import pandas as pd
import requests
import argparse
import logging
import json
import os
import sys
from typing import Dict, Any

# ==============================================================================
# Canvas Grade Synchronization ETL Pipeline (PrairieLearn to Canvas)
# Author: Zonglin Han
# Description: 
#   Extracts grade data from PrairieLearn CSV exports, transforms the schema 
#   to match Canvas SIS User IDs, and bulk loads into the Canvas Grading API.
# ==============================================================================

class GradeSyncPipeline:
    def __init__(self, canvas_domain: str, api_token: str, course_id: str, assignment_id: str, dry_run: bool = True):
        """
        Initialize the ETL pipeline with Canvas API credentials and endpoint routing.
        """
        self.canvas_domain = canvas_domain.rstrip('/')
        self.api_token = api_token
        self.course_id = course_id
        self.assignment_id = assignment_id
        self.dry_run = dry_run
        
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        # Configure professional logging instead of standard print statements
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger(__name__)

    def extract_and_transform(self, csv_path: str, user_col: str = 'Username', score_col: str = None) -> Dict[str, Any]:
        """
        Extract grades from the source CSV, clean the dataset, and transform 
        it into the Canvas API batch update payload format.
        """
        self.logger.info(f"Extracting dataset from source: {csv_path}")
        
        if not os.path.exists(csv_path):
            self.logger.error(f"Source file not found: {csv_path}")
            sys.exit(1)

        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            self.logger.error(f"Failed to parse CSV: {str(e)}")
            sys.exit(1)

        if user_col not in df.columns:
            self.logger.error(f"Primary key column '{user_col}' missing from schema. Aborting.")
            sys.exit(1)

        # Intelligent schema detection: Auto-detect the score column if not specified
        if not score_col:
            potential_score_cols = [col for col in df.columns if col != user_col]
            if not potential_score_cols:
                self.logger.error("No valid score metric column found in CSV schema.")
                sys.exit(1)
            score_col = potential_score_cols[0]
            self.logger.info(f"Auto-detected score column: '{score_col}'")

        # Data cleaning: drop records with missing IDs or scores
        initial_count = len(df)
        df_cleaned = df.dropna(subset=[user_col, score_col])
        if len(df_cleaned) < initial_count:
            self.logger.warning(f"Dropped {initial_count - len(df_cleaned)} corrupted or null rows during cleaning.")

        # Transform to Canvas Payload Structure
        payload = {}
        for _, row in df_cleaned.iterrows():
            student_sis_id = str(row[user_col]).strip()
            score = row[score_col]
            
            # Canvas format requirement: grade_data[sis_user_id:<id>][posted_grade]=<score>
            canvas_user_key = f"sis_user_id:{student_sis_id}"
            payload[f"grade_data[{canvas_user_key}][posted_grade]"] = score

        self.logger.info(f"Transformation complete. Payload constructed for {len(payload)} users.")
        return payload

    def load_to_canvas(self, payload: Dict[str, Any]) -> None:
        """
        Load the transformed payload into Canvas via the Submissions Bulk Update API.
        """
        if not payload:
            self.logger.warning("Empty payload. No data to sync.")
            return

        api_endpoint = f"{self.canvas_domain}/api/v1/courses/{self.course_id}/assignments/{self.assignment_id}/submissions/update_grades"
        self.logger.info(f"Target API Endpoint: {api_endpoint}")

        if self.dry_run:
            self.logger.info("[DRY RUN MODE ENABLED] Simulating API execution. No data will be written.")
            # Display a snapshot of the payload to verify data structures
            preview_items = list(payload.items())[:3]
            self.logger.info(f"Payload Snapshot (Top 3 records):\n{json.dumps(dict(preview_items), indent=2)}")
            return

        self.logger.info(f"Initiating HTTP POST request for {len(payload)} records...")
        try:
            # Send batch update request with a safety timeout
            response = requests.post(api_endpoint, headers=self.headers, data=payload, timeout=15)
            
            if response.status_code == 200:
                self.logger.info("Sync Successful! Grades dispatched to the Canvas asynchronous grading queue.")
                self.logger.info(f"Canvas Response: {response.json().get('workflow_state', 'queued')}")
            else:
                self.logger.error(f"API HTTP {response.status_code} Error: {response.text}")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Network exception occurred during Canvas sync: {str(e)}")

if __name__ == "__main__":
    # Command-line interface setup for DevOps/Cron integration
    parser = argparse.ArgumentParser(description="Automated PrairieLearn to Canvas Grade Sync Pipeline")
    parser.add_argument("--csv", required=True, help="Path to the PrairieLearn export CSV")
    parser.add_argument("--course", required=True, help="Target Canvas Course ID")
    parser.add_argument("--assignment", required=True, help="Target Canvas Assignment ID")
    parser.add_argument("--domain", default="https://canvas.ucdavis.edu", help="Canvas Domain URL (Default: UCD)")
    parser.add_argument("--commit", action="store_true", help="Disable dry-run mode and force push to Canvas")
    
    args = parser.parse_args()

    # Best practice: Retrieve sensitive API token from environment variables instead of hardcoding
    token = os.environ.get("CANVAS_API_TOKEN", "DUMMY_TOKEN_FOR_TESTING")

    # Initialize and execute pipeline
    pipeline = GradeSyncPipeline(
        canvas_domain=args.domain,
        api_token=token,
        course_id=args.course,
        assignment_id=args.assignment,
        dry_run=not args.commit
    )

    grade_payload = pipeline.extract_and_transform(args.csv)
    pipeline.load_to_canvas(grade_payload)