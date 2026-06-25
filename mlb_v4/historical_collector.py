#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import requests

import config

MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"


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


def parse_ip(value) -> float:
    if value is None:
        return 0.0
    s = str(value)
    if "." not in s:
        try:
            return float(s)
        except Exception:
            return 0.0
    whole, frac = s.split(".", 1)
    try:
        whole_val = int(whole)
    except Exception:
        whole_val = 0
    if frac == "1":
        frac_val = 1.0 / 3.0
    elif frac == "2":
        frac_val = 2.0 / 3.0
    else:
        try:
            frac_val = float(f"0.{frac}")
        except Exception:
            frac_val = 0.0
    return whole_val + frac_val


def extract_starting_pitcher_line(game_pk: int, side: str) -> dict:
    try:
        payload = safe_get(MLB_BOXSCORE_URL.format(game_pk=game_pk), {})
        team_box = payload.get("teams", {}).get(side, {})
        pitcher_ids = team_box.get("pitchers", []) or []
        players = team_box.get("players", {}) or {}
        if not pitcher_ids:
            return {}
        starter_id = pitcher_ids[0]
        starter = players.get(f"ID{starter_id}", {})
        stats = starter.get("stats", {}).get("pitching", {}) or {}
        return {
            "starter_name": starter.get("person", {}).get("fullName"),
            "starter_id": starter_id,
            "starter_ip": parse_ip(stats.get("inningsPitched")),
            "starter_er": int(stats.get("earnedRuns") or 0),
            "starter_bb": int(stats.get("baseOnBalls") or 0),
            "starter_so": int(stats.get("strikeOuts") or 0),
            "starter_h": int(stats.get("hits") or 0),
        }
    except Exception:
        return {}


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
            game_pk = game.get("gamePk")

            away_start = extract_starting_pitcher_line(game_pk, "away") if game_pk else {}
            home_start = extract_starting_pitcher_line(game_pk, "home") if game_pk else {}

            rows.append(
                {
                    "game_pk": game_pk,
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
                    "away_starter": away_start.get("starter_name") or away.get("probablePitcher", {}).get("fullName"),
                    "home_starter": home_start.get("starter_name") or home.get("probablePitcher", {}).get("fullName"),
                    "away_starter_id": away_start.get("starter_id") or away.get("probablePitcher", {}).get("id"),
                    "home_starter_id": home_start.get("starter_id") or home.get("probablePitcher", {}).get("id"),
                    "away_starter_ip": away_start.get("starter_ip"),
                    "home_starter_ip": home_start.get("starter_ip"),
                    "away_starter_er": away_start.get("starter_er"),
                    "home_starter_er": home_start.get("starter_er"),
                    "away_starter_bb": away_start.get("starter_bb"),
                    "home_starter_bb": home_start.get("starter_bb"),
                    "away_starter_so": away_start.get("starter_so"),
                    "home_starter_so": home_start.get("starter_so"),
                    "away_starter_h": away_start.get("starter_h"),
                    "home_starter_h": home_start.get("starter_h"),
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
