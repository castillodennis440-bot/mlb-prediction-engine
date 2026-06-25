#!/usr/bin/env python3
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import config


def parse_dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def avg(values, default=0.0):
    return sum(values) / len(values) if values else default


def pct(values, default=0.5):
    return sum(values) / len(values) if values else default


def team_snapshot(history: list[dict], location: str):
    last10 = history[-10:]
    last5_loc = [g for g in history if g["location"] == location][-5:]

    win_pct_10 = pct([g["win"] for g in last10], 0.5)
    rs_10 = avg([g["runs_for"] for g in last10], config.LEAGUE_RUNS_PER_TEAM)
    ra_10 = avg([g["runs_against"] for g in last10], config.LEAGUE_RUNS_PER_TEAM)
    loc_win_pct_5 = pct([g["win"] for g in last5_loc], 0.5)
    form_index = ((win_pct_10 - 0.5) * 2.0) + ((rs_10 - ra_10) / max(config.LEAGUE_RUNS_PER_TEAM, 1.0))

    return {
        "win_pct_10": round(win_pct_10, 4),
        "rs_10": round(rs_10, 4),
        "ra_10": round(ra_10, 4),
        "loc_win_pct_5": round(loc_win_pct_5, 4),
        "form_index": round(form_index, 4),
    }


def pitcher_snapshot(history: list[dict]):
    last5 = history[-5:]
    total_ip = sum(g["ip"] for g in last5)
    total_er = sum(g["er"] for g in last5)
    total_bb = sum(g["bb"] for g in last5)
    total_so = sum(g["so"] for g in last5)
    total_h = sum(g["h"] for g in last5)

    if total_ip <= 0:
        return {
            "era": 4.20,
            "whip": 1.30,
            "k9": 8.5,
            "starts": 0,
            "limited": 1,
        }

    era = (total_er * 9.0) / total_ip
    whip = (total_bb + total_h) / total_ip
    k9 = (total_so * 9.0) / total_ip
    starts = len(last5)
    limited = 1 if (starts < 4 or total_ip < 20.0) else 0

    return {
        "era": round(era, 4),
        "whip": round(whip, 4),
        "k9": round(k9, 4),
        "starts": starts,
        "limited": limited,
    }


def rest_days(history: list[dict], current_dt):
    if not history or not current_dt:
        return 4.0
    last_dt = history[-1].get("start_dt")
    if not last_dt:
        return 4.0
    delta_days = (current_dt.date() - last_dt.date()).days - 1
    return max(0.0, float(delta_days))


def build_rows(raw_path: Path) -> list[dict]:
    games = []
    with raw_path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("status") != "Final":
                continue
            if row.get("home_runs") is None or row.get("away_runs") is None:
                continue
            row["start_dt"] = parse_dt(row.get("start_time"))
            games.append(row)

    games.sort(key=lambda x: (x.get("game_date") or "", x.get("start_time") or ""))

    team_histories: dict[str, list[dict]] = {}
    pitcher_histories: dict[str, list[dict]] = {}
    rows = []

    for game in games:
        away_team = game["away_team"]
        home_team = game["home_team"]
        home_hist = team_histories.get(home_team, [])
        away_hist = team_histories.get(away_team, [])

        home_snapshot = team_snapshot(home_hist, "home")
        away_snapshot = team_snapshot(away_hist, "away")

        home_pitcher_key = str(game.get("home_starter_id") or game.get("home_starter") or "home_unknown")
        away_pitcher_key = str(game.get("away_starter_id") or game.get("away_starter") or "away_unknown")
        home_pitcher_snapshot = pitcher_snapshot(pitcher_histories.get(home_pitcher_key, []))
        away_pitcher_snapshot = pitcher_snapshot(pitcher_histories.get(away_pitcher_key, []))

        row = {
            "game_date": game["game_date"],
            "game_pk": game["game_pk"],
            "away_team": away_team,
            "home_team": home_team,
            "home_last10_win_pct": home_snapshot["win_pct_10"],
            "away_last10_win_pct": away_snapshot["win_pct_10"],
            "home_last10_rs": home_snapshot["rs_10"],
            "away_last10_rs": away_snapshot["rs_10"],
            "home_last10_ra": home_snapshot["ra_10"],
            "away_last10_ra": away_snapshot["ra_10"],
            "home_last5_home_win_pct": home_snapshot["loc_win_pct_5"],
            "away_last5_away_win_pct": away_snapshot["loc_win_pct_5"],
            "home_rest_days": rest_days(home_hist, game["start_dt"]),
            "away_rest_days": rest_days(away_hist, game["start_dt"]),
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
            "home_win": 1 if int(game["home_runs"]) > int(game["away_runs"]) else 0,
            "total_runs": int(game["home_runs"]) + int(game["away_runs"]),
            "home_runs": int(game["home_runs"]),
            "away_runs": int(game["away_runs"]),
        }
        rows.append(row)

        team_histories.setdefault(home_team, []).append(
            {
                "start_dt": game["start_dt"],
                "runs_for": int(game["home_runs"]),
                "runs_against": int(game["away_runs"]),
                "win": 1 if int(game["home_runs"]) > int(game["away_runs"]) else 0,
                "location": "home",
            }
        )
        team_histories.setdefault(away_team, []).append(
            {
                "start_dt": game["start_dt"],
                "runs_for": int(game["away_runs"]),
                "runs_against": int(game["home_runs"]),
                "win": 1 if int(game["away_runs"]) > int(game["home_runs"]) else 0,
                "location": "away",
            }
        )

        pitcher_histories.setdefault(home_pitcher_key, []).append(
            {
                "start_dt": game["start_dt"],
                "ip": float(game.get("home_starter_ip") or 0.0),
                "er": int(game.get("home_starter_er") or 0),
                "bb": int(game.get("home_starter_bb") or 0),
                "so": int(game.get("home_starter_so") or 0),
                "h": int(game.get("home_starter_h") or 0),
            }
        )
        pitcher_histories.setdefault(away_pitcher_key, []).append(
            {
                "start_dt": game["start_dt"],
                "ip": float(game.get("away_starter_ip") or 0.0),
                "er": int(game.get("away_starter_er") or 0),
                "bb": int(game.get("away_starter_bb") or 0),
                "so": int(game.get("away_starter_so") or 0),
                "h": int(game.get("away_starter_h") or 0),
            }
        )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build chronological MLB features from raw games JSONL.")
    parser.add_argument("--input", default=str(config.RAW_GAMES_PATH))
    parser.add_argument("--output", default=str(config.FEATURES_PATH))
    args = parser.parse_args()

    raw_path = Path(args.input)
    output_path = Path(args.output)
    rows = build_rows(raw_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError("No feature rows were generated.")

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} feature rows to {output_path}")


if __name__ == "__main__":
    main()
