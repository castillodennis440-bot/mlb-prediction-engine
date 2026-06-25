#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import requests

import config


def safe_get(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def normalize_name(name: str) -> str:
    return name.strip() if name else ""


def daterange(start_date: datetime, end_date: datetime):
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += timedelta(days=1)


def fetch_day(day: datetime) -> list[dict]:
    payload = safe_get(
        config.MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "date": day.strftime("%Y-%m-%d"),
            "gameTypes": "R",
            "hydrate": "probablePitcher,venue",
        },
    )

    rows = []
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})
            away_team = normalize_name(away.get("team", {}).get("name", ""))
            home_team = normalize_name(home.get("team", {}).get("name", ""))
            away_runs = away.get("score")
            home_runs = home.get("score")
            rows.append(
                {
                    "game_pk": game.get("gamePk"),
                    "game_date": date_row.get("date"),
                    "start_time": game.get("gameDate"),
                    "status": game.get("status", {}).get("abstractGameState"),
                    "venue": game.get("venue", {}).get("name"),
                    "away_team": away_team,
                    "home_team": home_team,
                    "away_team_id": away.get("team", {}).get("id"),
                    "home_team_id": home.get("team", {}).get("id"),
                    "away_runs": away_runs,
                    "home_runs": home_runs,
                    "away_starter": away.get("probablePitcher", {}).get("fullName"),
                    "home_starter": home.get("probablePitcher", {}).get("fullName"),
                    "away_starter_id": away.get("probablePitcher", {}).get("id"),
                    "home_starter_id": home.get("probablePitcher", {}).get("id"),
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect historical MLB games into JSONL.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", default=str(config.RAW_GAMES_PATH))
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")

    output_path = Path(args.output)

    rows = []
    for day in daterange(start_date, end_date):
        rows.extend(fetch_day(day))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    print(f"Wrote {len(rows)} games to {output_path}")


if __name__ == "__main__":
    main()
