#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib
import requests

import config
from feature_builder import team_snapshot, rest_days, pitcher_snapshot, parse_dt
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
    "home_pitcher_last5_era",
    "away_pitcher_last5_era",
    "home_pitcher_last5_whip",
    "away_pitcher_last5_whip",
    "home_pitcher_last5_k9",
    "away_pitcher_last5_k9",
    "home_pitcher_starts_hist",
    "away_pitcher_starts_hist",
    "home_pitcher_limited_sample",
    "away_pitcher_limited_sample",
]


def safe_get(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def load_history(raw_path: Path):
    team_history = {}
    pitcher_history = {}
    with raw_path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("status") != "Final":
                continue
            if row.get("home_runs") is None or row.get("away_runs") is None:
                continue
            start_dt = parse_dt(row.get("start_time"))
            team_history.setdefault(row["home_team"], []).append(
                {
                    "start_dt": start_dt,
                    "runs_for": int(row["home_runs"]),
                    "runs_against": int(row["away_runs"]),
                    "win": 1 if int(row["home_runs"]) > int(row["away_runs"]) else 0,
                    "location": "home",
                }
            )
            team_history.setdefault(row["away_team"], []).append(
                {
                    "start_dt": start_dt,
                    "runs_for": int(row["away_runs"]),
                    "runs_against": int(row["home_runs"]),
                    "win": 1 if int(row["away_runs"]) > int(row["home_runs"]) else 0,
                    "location": "away",
                }
            )

            home_pitcher_key = str(row.get("home_starter_id") or row.get("home_starter") or "home_unknown")
            away_pitcher_key = str(row.get("away_starter_id") or row.get("away_starter") or "away_unknown")
            pitcher_history.setdefault(home_pitcher_key, []).append(
                {
                    "start_dt": start_dt,
                    "ip": float(row.get("home_starter_ip") or 0.0),
                    "er": int(row.get("home_starter_er") or 0),
                    "bb": int(row.get("home_starter_bb") or 0),
                    "so": int(row.get("home_starter_so") or 0),
                    "h": int(row.get("home_starter_h") or 0),
                }
            )
            pitcher_history.setdefault(away_pitcher_key, []).append(
                {
                    "start_dt": start_dt,
                    "ip": float(row.get("away_starter_ip") or 0.0),
                    "er": int(row.get("away_starter_er") or 0),
                    "bb": int(row.get("away_starter_bb") or 0),
                    "so": int(row.get("away_starter_so") or 0),
                    "h": int(row.get("away_starter_h") or 0),
                }
            )

    for team in team_history:
        team_history[team].sort(key=lambda x: x["start_dt"])
    for pitcher in pitcher_history:
        pitcher_history[pitcher].sort(key=lambda x: x["start_dt"])

    return team_history, pitcher_history


def fetch_today_schedule_map(day: datetime) -> dict:
    payload = safe_get(
        config.MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "date": day.strftime("%Y-%m-%d"),
            "gameTypes": "R",
            "hydrate": "probablePitcher,venue",
        },
    )
    mapping = {}
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            away = normalize_name(game.get("teams", {}).get("away", {}).get("team", {}).get("name", ""))
            home = normalize_name(game.get("teams", {}).get("home", {}).get("team", {}).get("name", ""))
            mapping[(away, home)] = {
                "away_starter": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName"),
                "home_starter": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName"),
                "away_starter_id": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id"),
                "home_starter_id": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id"),
                "venue": game.get("venue", {}).get("name"),
                "start_time": game.get("gameDate"),
            }
    return mapping


def build_today_rows(day: datetime, team_history: dict, pitcher_history: dict):
    schedule_map = fetch_today_schedule_map(day)
    rows = []
    for row in fetch_day(day):
        away_team = normalize_name(row["away_team"])
        home_team = normalize_name(row["home_team"])
        schedule_info = schedule_map.get((away_team, home_team), {})
        start_dt = parse_dt(schedule_info.get("start_time") or row.get("start_time"))
        home_hist = team_history.get(home_team, [])
        away_hist = team_history.get(away_team, [])
        home_snapshot = team_snapshot(home_hist, "home")
        away_snapshot = team_snapshot(away_hist, "away")

        home_pitcher_key = str(schedule_info.get("home_starter_id") or schedule_info.get("home_starter") or "home_unknown")
        away_pitcher_key = str(schedule_info.get("away_starter_id") or schedule_info.get("away_starter") or "away_unknown")
        home_pitcher_snapshot = pitcher_snapshot(pitcher_history.get(home_pitcher_key, []))
        away_pitcher_snapshot = pitcher_snapshot(pitcher_history.get(away_pitcher_key, []))

        feature_row = {
            "game_date": row.get("game_date"),
            "away_team": away_team,
            "home_team": home_team,
            "start_time": schedule_info.get("start_time") or row.get("start_time"),
            "venue": schedule_info.get("venue") or row.get("venue"),
            "away_starter": schedule_info.get("away_starter"),
            "home_starter": schedule_info.get("home_starter"),
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
            "home_pitcher_last5_era": home_pitcher_snapshot["era"],
            "away_pitcher_last5_era": away_pitcher_snapshot["era"],
            "home_pitcher_last5_whip": home_pitcher_snapshot["whip"],
            "away_pitcher_last5_whip": away_pitcher_snapshot["whip"],
            "home_pitcher_last5_k9": home_pitcher_snapshot["k9"],
            "away_pitcher_last5_k9": away_pitcher_snapshot["k9"],
            "home_pitcher_starts_hist": home_pitcher_snapshot["starts"],
            "away_pitcher_starts_hist": away_pitcher_snapshot["starts"],
            "home_pitcher_limited_sample": home_pitcher_snapshot["limited"],
            "away_pitcher_limited_sample": away_pitcher_snapshot["limited"],
        }
        rows.append(feature_row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Score today's MLB games with V4.1 models.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--raw", default=str(config.RAW_GAMES_PATH))
    parser.add_argument("--output", default=str(config.TODAY_PREDICTIONS_PATH))
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d")
    team_history, pitcher_history = load_history(Path(args.raw))
    rows = build_today_rows(day, team_history, pitcher_history)

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
