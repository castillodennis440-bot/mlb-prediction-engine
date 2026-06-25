#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib

import config
from feature_builder import team_snapshot, rest_days, parse_dt
from historical_collector import fetch_day, normalize_name

FEATURE_COLUMNS = [
    "home_last10_win_pct",
    "away_last10_win_pct",
    "home_last10_rs",
    "away_last10_rs",
    "home_last10_ra",
    "away_last10_ra",
    "home_last5_home_win_pct",
    "away_last5_away_win_pct",
    "home_rest_days",
    "away_rest_days",
    "home_form_index",
    "away_form_index",
]


def load_history(raw_path: Path):
    history = {}
    with raw_path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("status") != "Final":
                continue
            if row.get("home_runs") is None or row.get("away_runs") is None:
                continue
            start_dt = parse_dt(row.get("start_time"))
            history.setdefault(row["home_team"], []).append(
                {
                    "start_dt": start_dt,
                    "runs_for": int(row["home_runs"]),
                    "runs_against": int(row["away_runs"]),
                    "win": 1 if int(row["home_runs"]) > int(row["away_runs"]) else 0,
                    "location": "home",
                }
            )
            history.setdefault(row["away_team"], []).append(
                {
                    "start_dt": start_dt,
                    "runs_for": int(row["away_runs"]),
                    "runs_against": int(row["home_runs"]),
                    "win": 1 if int(row["away_runs"]) > int(row["home_runs"]) else 0,
                    "location": "away",
                }
            )
    for team in history:
        history[team].sort(key=lambda x: x["start_dt"])
    return history


def build_today_rows(day: datetime, history: dict):
    rows = []
    for row in fetch_day(day):
        away_team = normalize_name(row["away_team"])
        home_team = normalize_name(row["home_team"])
        start_dt = parse_dt(row.get("start_time"))
        home_hist = history.get(home_team, [])
        away_hist = history.get(away_team, [])
        home_snapshot = team_snapshot(home_hist, "home")
        away_snapshot = team_snapshot(away_hist, "away")
        feature_row = {
            "game_date": row.get("game_date"),
            "away_team": away_team,
            "home_team": home_team,
            "start_time": row.get("start_time"),
            "venue": row.get("venue"),
            "away_starter": row.get("away_starter"),
            "home_starter": row.get("home_starter"),
            "home_last10_win_pct": home_snapshot["win_pct_10"],
            "away_last10_win_pct": away_snapshot["win_pct_10"],
            "home_last10_rs": home_snapshot["rs_10"],
            "away_last10_rs": away_snapshot["rs_10"],
            "home_last10_ra": home_snapshot["ra_10"],
            "away_last10_ra": away_snapshot["ra_10"],
            "home_last5_home_win_pct": home_snapshot["loc_win_pct_5"],
            "away_last5_away_win_pct": away_snapshot["loc_win_pct_5"],
            "home_rest_days": rest_days(home_hist, start_dt),
            "away_rest_days": rest_days(away_hist, start_dt),
            "home_form_index": home_snapshot["form_index"],
            "away_form_index": away_snapshot["form_index"],
        }
        rows.append(feature_row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Score today's MLB games with V4 models.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--raw", default=str(config.RAW_GAMES_PATH))
    parser.add_argument("--output", default=str(config.TODAY_PREDICTIONS_PATH))
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d")
    history = load_history(Path(args.raw))
    rows = build_today_rows(day, history)

    moneyline_model = joblib.load(config.MODEL_DIR / "moneyline_model.joblib")
    calibrator = joblib.load(config.MODEL_DIR / "moneyline_calibrator.joblib")
    totals_model = joblib.load(config.MODEL_DIR / "totals_model.joblib")

    output = []
    for row in rows:
        X = [[float(row[c]) for c in FEATURE_COLUMNS]]
        raw_home = float(moneyline_model.predict_proba(X)[0][1])
        cal_home = float(calibrator.predict([raw_home])[0])
        total_pred = float(totals_model.predict(X)[0])
        home_share = max(0.35, min(0.65, cal_home))
        home_runs = round(total_pred * home_share, 2)
        away_runs = round(total_pred - home_runs, 2)
        output.append(
            {
                "game_date": row["game_date"],
                "away_team": row["away_team"],
                "home_team": row["home_team"],
                "start_time": row["start_time"],
                "venue": row["venue"],
                "away_starter": row.get("away_starter"),
                "home_starter": row.get("home_starter"),
                "home_win_prob_raw": round(raw_home, 6),
                "home_win_prob_calibrated": round(cal_home, 6),
                "away_win_prob_calibrated": round(1.0 - cal_home, 6),
                "total_runs_pred": round(total_pred, 4),
                "suggested_home_score": home_runs,
                "suggested_away_score": away_runs,
            }
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(output)} predictions to {output_path}")


if __name__ == "__main__":
    main()
