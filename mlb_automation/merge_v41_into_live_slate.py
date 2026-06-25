#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def norm(name: str) -> str:
    return (name or "").strip().lower()


def clamp_runs(value: float) -> float:
    return max(0.2, round(float(value), 3))


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge V4.1 predictions into the live odds slate.")
    parser.add_argument("--v41", required=True, help="Path to mlb_v4 today_predictions.json")
    parser.add_argument("--live", required=True, help="Path to mlb_automation live_slate.json")
    parser.add_argument("--output", required=True, help="Path to output merged slate")
    args = parser.parse_args()

    v41_rows = json.loads(Path(args.v41).read_text(encoding="utf-8"))
    live_payload = json.loads(Path(args.live).read_text(encoding="utf-8"))

    pred_map = {
        (norm(r.get("away_team")), norm(r.get("home_team"))): r
        for r in v41_rows
    }

    merged_games = []
    unmatched = []

    for game in live_payload.get("games", []):
        key = (norm(game.get("away_team")), norm(game.get("home_team")))
        pred = pred_map.get(key)

        merged = dict(game)
        merged.setdefault("source_meta", {})

        if pred:
            home9 = clamp_runs(pred.get("suggested_home_score", game.get("lambda_home_9", 4.3)))
            away9 = clamp_runs(pred.get("suggested_away_score", game.get("lambda_away_9", 4.3)))
            home5 = clamp_runs(round(home9 * 5.0 / 9.0, 3))
            away5 = clamp_runs(round(away9 * 5.0 / 9.0, 3))

            merged["lambda_home_9"] = home9
            merged["lambda_away_9"] = away9
            merged["lambda_home_5"] = home5
            merged["lambda_away_5"] = away5
            merged["venue"] = pred.get("venue") or merged.get("venue")
            merged["away_starter"] = pred.get("away_starter") or merged.get("away_starter")
            merged["home_starter"] = pred.get("home_starter") or merged.get("home_starter")
            merged["source_meta"]["v41_home_win_prob"] = pred.get("home_win_prob_calibrated")
            merged["source_meta"]["v41_away_win_prob"] = pred.get("away_win_prob_calibrated")
            merged["source_meta"]["v41_total_runs_pred"] = pred.get("total_runs_pred")
            merged["source_meta"]["v41_model_tag"] = "v4.1"
            merged["source_meta"]["v41_match_status"] = "matched"
        else:
            merged["source_meta"]["v41_model_tag"] = "v4.1"
            merged["source_meta"]["v41_match_status"] = "unmatched"
            unmatched.append(
                {
                    "away_team": game.get("away_team"),
                    "home_team": game.get("home_team"),
                    "reason": "No V4.1 prediction match found; live slate values kept",
                }
            )

        merged_games.append(merged)

    out_payload = {
        "date": live_payload.get("date"),
        "generated_at_utc": live_payload.get("generated_at_utc"),
        "source": {
            "provider": "OddsPapi + MLB V4.1",
            "bookmaker": live_payload.get("source", {}).get("bookmaker", "pinnacle"),
            "sportId": live_payload.get("source", {}).get("sportId", 13),
        },
        "games": merged_games,
        "skipped": list(live_payload.get("skipped", [])) + unmatched,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")
    print(f"Merged {len(merged_games)} games into {output_path}")
    if unmatched:
        print(f"Unmatched live games retained: {len(unmatched)}")


if __name__ == "__main__":
    main()
