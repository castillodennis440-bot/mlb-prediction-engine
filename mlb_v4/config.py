from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
FEATURE_DIR = DATA_DIR / "features"
MODEL_DIR = ROOT / "models"
OUTPUT_DIR = ROOT / "output"

RAW_DIR.mkdir(parents=True, exist_ok=True)
FEATURE_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RAW_GAMES_PATH = RAW_DIR / "games.jsonl"
FEATURES_PATH = FEATURE_DIR / "game_features.csv"
TODAY_PREDICTIONS_PATH = OUTPUT_DIR / "today_predictions.json"

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_PEOPLE_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}/stats"

LEAGUE_RUNS_PER_TEAM = 4.35
MIN_HISTORY_GAMES = 3
