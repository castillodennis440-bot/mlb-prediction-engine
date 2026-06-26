import argparse
import json
import math 
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import requests


ALLOWED_MARKETS = {
    "Moneyline",
    "Run Line",
    "Total",
    "F5 Winner",
    "F5 Handicap",
}


def log(message):
    print(f"[supabase-push] {message}")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_records(payload):
    """
    Accepts multiple possible JSON shapes.

    Supported examples:
    - [ {...}, {...} ]
    - { "games": [ ... ] }
    - { "slate": [ ... ] }
    - { "predictions": [ ... ] }
    - { "rows": [ ... ] }
    - { "data": [ ... ] }
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ["predictions", "picks", "slate", "games", "rows", "data", "items"]:
            value = payload.get(key)
            if isinstance(value, list):
                return value

    return []


def get_first(row, keys, default=None):
    for key in keys:
        if key in row and row[key] not in [None, "", "null", "None"]:
            return row[key]
    return default


def to_float(value, default=None):
    if value in [None, "", "null", "None"]:
        return default
    try:
        return float(value)
    except Exception:
        return default


def american_to_decimal(odds):
    odds = to_float(odds)
    if odds is None:
        return None

    # Already decimal
    if 1.01 <= odds <= 20:
        return round(odds, 4)

    # American odds
    if odds > 0:
        return round(1 + odds / 100, 4)

    if odds < 0:
        return round(1 + 100 / abs(odds), 4)

    return None


def normalize_market(value):
    if not value:
        return None

    v = str(value).strip().lower()

    mapping = {
        "moneyline": "Moneyline",
        "ml": "Moneyline",
        "full game moneyline": "Moneyline",

        "run line": "Run Line",
        "runline": "Run Line",
        "handicap": "Run Line",
        "spread": "Run Line",

        "total": "Total",
        "totals": "Total",
        "over/under": "Total",
        "over under": "Total",

        "f5 winner": "F5 Winner",
        "first 5 winner": "F5 Winner",
        "first five winner": "F5 Winner",
        "f5 moneyline": "F5 Winner",
        "first 5 moneyline": "F5 Winner",

        "f5 handicap": "F5 Handicap",
        "first 5 handicap": "F5 Handicap",
        "f5 spread": "F5 Handicap",
        "first 5 spread": "F5 Handicap",
    }

    return mapping.get(v)


def normalize_confidence(value, edge=None):
    if value:
        v = str(value).strip().lower()
        if v in ["high", "strong", "a", "top"]:
            return "High"
        if v in ["medium", "med", "normal", "b"]:
            return "Medium"
        if v in ["value", "low", "c"]:
            return "Value"

    edge = to_float(edge, 0)
    if edge >= 5:
        return "High"
    if edge >= 3:
        return "Medium"
    return "Value"


def poisson_pmf(lam, k):
    if lam is None:
        return 0.0
    try:
        lam = float(lam)
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except Exception:
        return 0.0


def poisson_distribution(lam, max_runs=20):
    probs = [poisson_pmf(lam, k) for k in range(max_runs + 1)]
    total = sum(probs)
    if total > 0:
        probs = [p / total for p in probs]
    return probs


def full_game_win_probabilities(lambda_away, lambda_home):
    away_dist = poisson_distribution(lambda_away, 20)
    home_dist = poisson_distribution(lambda_home, 20)

    away_win = 0.0
    home_win = 0.0
    tie = 0.0

    for away_runs, pa in enumerate(away_dist):
        for home_runs, ph in enumerate(home_dist):
            p = pa * ph
            if away_runs > home_runs:
                away_win += p
            elif home_runs > away_runs:
                home_win += p
            else:
                tie += p

    # MLB cannot end tied, so distribute tie probability proportionally.
    non_tie = away_win + home_win
    if non_tie > 0:
        away_win = away_win / non_tie
        home_win = home_win / non_tie

    return away_win, home_win


def total_probabilities(lambda_away, lambda_home, line):
    away_dist = poisson_distribution(lambda_away, 20)
    home_dist = poisson_distribution(lambda_home, 20)

    over = 0.0
    under = 0.0
    push = 0.0

    for away_runs, pa in enumerate(away_dist):
        for home_runs, ph in enumerate(home_dist):
            total_runs = away_runs + home_runs
            p = pa * ph

            if total_runs > line:
                over += p
            elif total_runs < line:
                under += p
            else:
                push += p

    return over, under, push


def run_line_probability(lambda_away, lambda_home, team, line):
    away_dist = poisson_distribution(lambda_away, 20)
    home_dist = poisson_distribution(lambda_home, 20)

    cover = 0.0

    for away_runs, pa in enumerate(away_dist):
        for home_runs, ph in enumerate(home_dist):
            p = pa * ph

            if team == "away":
                if away_runs + line > home_runs:
                    cover += p

            if team == "home":
                if home_runs + line > away_runs:
                    cover += p

    return cover


def build_reasoning(row, base_reason):
    parts = []

    if base_reason:
        parts.append(base_reason)

    for key in [
        "starter_note",
        "model_blend_note",
        "weather_note",
        "bullpen_note",
        "away_form_note",
        "home_form_note",
    ]:
        value = row.get(key)
        if value:
            parts.append(str(value))

    return " | ".join(parts)[:900]


def make_candidate(
    row,
    game_date,
    model_version,
    market_type,
    selection,
    line_value,
    odds_decimal,
    model_probability,
    base_reason,
):
    odds_decimal = to_float(odds_decimal)
    model_probability = to_float(model_probability)

    if not selection:
        return None

    if market_type not in ALLOWED_MARKETS:
        return None

    if odds_decimal is None or odds_decimal <= 1:
        return None

    if model_probability is None or model_probability <= 0 or model_probability >= 1:
        return None

    implied_probability = 1 / odds_decimal
    adjusted_edge = (model_probability - implied_probability) * 100

    # Decimal odds EV calculation.
    ev = ((model_probability * (odds_decimal - 1)) - (1 - model_probability)) * 100

    # Only publish positive-EV/value candidates.
    if ev <= 0:
        return None

    confidence_tier = normalize_confidence(None, adjusted_edge)

    source_meta = row.get("source_meta") or {}

    away_team = row.get("away_team")
    home_team = row.get("home_team")

    return {
        "model_version": model_version,
        "game_date": game_date,
        "start_time": source_meta.get("start_time") or row.get("start_time"),
        "away_team": away_team,
        "home_team": home_team,
        "venue": row.get("venue"),
        "away_starter": row.get("away_starter") or "TBD",
        "home_starter": row.get("home_starter") or "TBD",
        "market_type": market_type,
        "selection": selection,
        "line_value": line_value,
        "odds_decimal": round(float(odds_decimal), 4),
        "fair_probability": round(implied_probability * 100, 2),
        "predicted_probability": round(model_probability * 100, 2),
        "adjusted_edge": round(adjusted_edge, 2),
        "ev": round(ev, 2),
        "stake_units": 1.0,
        "confidence_tier": confidence_tier,
        "reasoning": build_reasoning(row, base_reason),
        "status": "pending",
        "archived": False,
        "deleted_at": None,
    }


def build_prediction_candidates(row, game_date, model_version):
    """
    Converts one game from final_scoring_slate.json into app predictions.

    This version supports the current model output shape:

    {
      "away_team": "...",
      "home_team": "...",
      "lambda_away_9": ...,
      "lambda_home_9": ...,
      "odds": {
        "fg_ml": {"away": ..., "home": ...},
        "fg_total": {"line": ..., "over": ..., "under": ...},
        "fg_rl": {"home_line": ..., "away_line": ..., "home_odds": ..., "away_odds": ...}
      }
    }
    """

    candidates = []

    away_team = row.get("away_team")
    home_team = row.get("home_team")

    if not away_team or not home_team:
        return []

    odds = row.get("odds") or {}

    lambda_away_9 = to_float(row.get("lambda_away_9"))
    lambda_home_9 = to_float(row.get("lambda_home_9"))

    if lambda_away_9 is None or lambda_home_9 is None:
        return []

    base_reason = row.get("model_blend_note") or "Model-derived value from projected run environment and market odds."

    # Full game moneyline
    fg_ml = odds.get("fg_ml") or {}
    away_win_prob, home_win_prob = full_game_win_probabilities(lambda_away_9, lambda_home_9)

    candidates.append(
        make_candidate(
            row=row,
            game_date=game_date,
            model_version=model_version,
            market_type="Moneyline",
            selection=f"{away_team} ML",
            line_value=None,
            odds_decimal=fg_ml.get("away"),
            model_probability=away_win_prob,
            base_reason=base_reason,
        )
    )

    candidates.append(
        make_candidate(
            row=row,
            game_date=game_date,
            model_version=model_version,
            market_type="Moneyline",
            selection=f"{home_team} ML",
            line_value=None,
            odds_decimal=fg_ml.get("home"),
            model_probability=home_win_prob,
            base_reason=base_reason,
        )
    )

    # Full game total
    fg_total = odds.get("fg_total") or {}
    total_line = to_float(fg_total.get("line"))

    if total_line is not None:
        over_prob, under_prob, push_prob = total_probabilities(
            lambda_away_9,
            lambda_home_9,
            total_line,
        )

        candidates.append(
            make_candidate(
                row=row,
                game_date=game_date,
                model_version=model_version,
                market_type="Total",
                selection=f"Over {total_line:g}",
                line_value=total_line,
                odds_decimal=fg_total.get("over"),
                model_probability=over_prob,
                base_reason=base_reason,
            )
        )

        candidates.append(
            make_candidate(
                row=row,
                game_date=game_date,
                model_version=model_version,
                market_type="Total",
                selection=f"Under {total_line:g}",
                line_value=total_line,
                odds_decimal=fg_total.get("under"),
                model_probability=under_prob,
                base_reason=base_reason,
            )
        )

    # Full game run line
    fg_rl = odds.get("fg_rl") or {}

    away_line = to_float(fg_rl.get("away_line"))
    home_line = to_float(fg_rl.get("home_line"))

    if away_line is not None:
        away_cover_prob = run_line_probability(
            lambda_away_9,
            lambda_home_9,
            "away",
            away_line,
        )

        candidates.append(
            make_candidate(
                row=row,
                game_date=game_date,
                model_version=model_version,
                market_type="Run Line",
                selection=f"{away_team} {away_line:+g}",
                line_value=away_line,
                odds_decimal=fg_rl.get("away_odds"),
                model_probability=away_cover_prob,
                base_reason=base_reason,
            )
        )

    if home_line is not None:
        home_cover_prob = run_line_probability(
            lambda_away_9,
            lambda_home_9,
            "home",
            home_line,
        )

        candidates.append(
            make_candidate(
                row=row,
                game_date=game_date,
                model_version=model_version,
                market_type="Run Line",
                selection=f"{home_team} {home_line:+g}",
                line_value=home_line,
                odds_decimal=fg_rl.get("home_odds"),
                model_probability=home_cover_prob,
                base_reason=base_reason,
            )
        )

    # Remove empty/non-value candidates.
    candidates = [c for c in candidates if c is not None]

    # Keep only strongest positive-EV candidates per game to avoid flooding the app.
    candidates.sort(key=lambda item: item.get("ev", 0), reverse=True)

    return candidates[:2]
    """
    Converts one row from final_scoring_slate.json into zero or more app predictions.

    This is intentionally flexible because model output formats can evolve.
    It looks for common field names and only sends rows with enough information.
    """
    candidates = []

    away_team = get_first(row, [
        "away_team", "away", "visitor", "visitor_team", "team_away"
    ])

    home_team = get_first(row, [
        "home_team", "home", "home_team_name", "team_home"
    ])

    venue = get_first(row, ["venue", "stadium", "ballpark"])
    start_time = get_first(row, ["start_time", "game_time", "commence_time"])
    away_starter = get_first(row, ["away_starter", "away_pitcher", "visitor_starter"], "TBD")
    home_starter = get_first(row, ["home_starter", "home_pitcher"], "TBD")

    reasoning = get_first(row, [
        "reasoning", "edge_reason", "model_reason", "notes", "summary"
    ], "")

    # Generic single-pick format
    selection = get_first(row, [
        "selection",
        "pick",
        "recommended_pick",
        "prediction",
        "bet",
        "play"
    ])

    market = normalize_market(get_first(row, [
        "market_type",
        "market",
        "bet_type"
    ]))

    odds = american_to_decimal(get_first(row, [
        "odds_decimal",
        "decimal_odds",
        "odds",
        "price",
        "book_odds",
        "pinnacle_odds"
    ]))

    line_value = to_float(get_first(row, [
        "line_value",
        "line",
        "handicap",
        "total",
        "spread"
    ]))

    predicted_probability = to_float(get_first(row, [
        "predicted_probability",
        "model_probability",
        "probability",
        "prob",
        "win_probability"
    ]))

    fair_probability = to_float(get_first(row, [
        "fair_probability",
        "market_probability",
        "implied_probability",
        "fair_prob"
    ]))

    adjusted_edge = to_float(get_first(row, [
        "adjusted_edge",
        "edge",
        "model_edge"
    ]), 0)

    ev = to_float(get_first(row, [
        "ev",
        "expected_value",
        "expected_value_pct"
    ]), adjusted_edge)

    stake_units = to_float(get_first(row, [
        "stake_units",
        "stake",
        "units"
    ]), 1.0)

    confidence_tier = normalize_confidence(
        get_first(row, ["confidence_tier", "confidence"]),
        adjusted_edge,
    )

    if selection and market in ALLOWED_MARKETS and odds and odds > 1:
        candidates.append({
            "model_version": model_version,
            "game_date": game_date,
            "start_time": start_time,
            "away_team": away_team,
            "home_team": home_team,
            "venue": venue,
            "away_starter": away_starter,
            "home_starter": home_starter,
            "market_type": market,
            "selection": str(selection),
            "line_value": line_value,
            "odds_decimal": odds,
            "fair_probability": fair_probability,
            "predicted_probability": predicted_probability,
            "adjusted_edge": adjusted_edge,
            "ev": ev,
            "stake_units": stake_units,
            "confidence_tier": confidence_tier,
            "reasoning": reasoning,
            "status": "pending",
            "archived": False,
            "deleted_at": None,
        })

    return candidates


class SupabaseRest:
    def __init__(self, url, service_role_key):
        self.base = url.rstrip("/")
        self.rest = f"{self.base}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def insert(self, table, row):
        response = requests.post(
            f"{self.rest}/{table}",
            headers=self.headers,
            json=row,
            timeout=30,
        )
        if response.status_code not in [200, 201]:
            raise RuntimeError(f"Insert failed {table}: {response.status_code} {response.text}")
        return response.json()

    def patch_by_id(self, table, row_id, row):
        response = requests.patch(
            f"{self.rest}/{table}?id=eq.{quote(str(row_id))}",
            headers=self.headers,
            json=row,
            timeout=30,
        )
        if response.status_code not in [200, 204]:
            raise RuntimeError(f"Patch failed {table}: {response.status_code} {response.text}")
        try:
            return response.json()
        except Exception:
            return []

    def find_duplicate_prediction(self, prediction):
        """
        Duplicate logic:
        game_date + away_team + home_team + market_type + selection + line_value + model_version
        """
        params = {
            "game_date": prediction.get("game_date"),
            "away_team": prediction.get("away_team"),
            "home_team": prediction.get("home_team"),
            "market_type": prediction.get("market_type"),
            "selection": prediction.get("selection"),
            "model_version": prediction.get("model_version"),
        }

        filters = []
        for key, value in params.items():
            if value not in [None, ""]:
                filters.append(f"{key}=eq.{quote(str(value))}")

        line_value = prediction.get("line_value")
        if line_value is None:
            filters.append("line_value=is.null")
        else:
            filters.append(f"line_value=eq.{quote(str(line_value))}")

        query = "&".join(filters) + "&select=id&limit=1"

        response = requests.get(
            f"{self.rest}/predictions?{query}",
            headers=self.headers,
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Duplicate check failed: {response.status_code} {response.text}")

        data = response.json()
        if data:
            return data[0]["id"]

        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--game-date", required=True)
    parser.add_argument("--model-version", default="V4.1")
    args = parser.parse_args()

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise RuntimeError("Missing SUPABASE_URL")
    if not service_role_key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY")

    log(f"Reading input file: {args.input}")

    payload = load_json(args.input)
    records = find_records(payload)

    log(f"Found {len(records)} raw slate records")

    predictions = []
    for row in records:
        if isinstance(row, dict):
            predictions.extend(
                build_prediction_candidates(
                    row=row,
                    game_date=args.game_date,
                    model_version=args.model_version,
                )
            )

    log(f"Built {len(predictions)} prediction candidates")

    db = SupabaseRest(supabase_url, service_role_key)

    inserted = 0
    updated = 0
    skipped = 0

    for prediction in predictions:
        if not prediction.get("away_team") or not prediction.get("home_team"):
            skipped += 1
            log(f"Skipping prediction missing teams: {prediction}")
            continue

        duplicate_id = db.find_duplicate_prediction(prediction)

        if duplicate_id:
            db.patch_by_id("predictions", duplicate_id, prediction)
            updated += 1
            log(f"Updated existing prediction: {prediction['selection']}")
        else:
            db.insert("predictions", prediction)
            inserted += 1
            log(f"Inserted prediction: {prediction['selection']}")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": args.input,
        "game_date": args.game_date,
        "model_version": args.model_version,
        "raw_records": len(records),
        "prediction_candidates": len(predictions),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }

    os.makedirs("mlb_automation", exist_ok=True)
    with open("mlb_automation/supabase_push_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    log(f"Summary: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
