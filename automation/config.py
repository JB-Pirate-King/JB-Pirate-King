"""
automation/config.py
환경변수 기반 설정 — .env.example 참고
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── MLflow ───────────────────────────────────────────────────────────────────
MLFLOW_TRACKING_URI   = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT     = os.getenv("MLFLOW_EXPERIMENT", "ais-anomaly-detection")

# ─── Slack ────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN       = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL         = os.getenv("SLACK_CHANNEL", "#ais-training-alerts")

# ─── Discord ─────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL   = os.getenv("DISCORD_WEBHOOK_URL", "")

# ─── Notion ───────────────────────────────────────────────────────────────────
NOTION_API_KEY        = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID    = os.getenv("NOTION_DATABASE_ID", "")

# ─── Google Sheets ────────────────────────────────────────────────────────────
GSHEETS_CREDS_FILE    = os.getenv("GSHEETS_CREDS_FILE", "automation/credentials.json")
GSHEETS_SPREADSHEET_ID = os.getenv("GSHEETS_SPREADSHEET_ID", "")
GSHEETS_SHEET_NAME    = os.getenv("GSHEETS_SHEET_NAME", "pipeline_results")

# ─── GitHub ───────────────────────────────────────────────────────────────────
GITHUB_TOKEN          = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO           = os.getenv("GITHUB_REPO", "heahgo/JB-Pirate-King")

# ─── Pipeline ─────────────────────────────────────────────────────────────────
BASE_DIR   = os.getenv("BASE_DIR", "D:\\")
DATA_FILE  = os.getenv(
    "DATA_FILE",
    "D:\\ais_data\\preprocessed\\2025\\ais_preprocessed_2025.csv"
)
MODELS_DIR = os.getenv("MODELS_DIR", "D:\\ais_models")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "D:\\ais_output\\pipeline")
