#!/usr/bin/env python3
import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist

import requests

BASE_URL = os.getenv("ODDSPAPI_BASE_URL", "https://api.oddspapi.io/v4")
SPORT_ID = int(os.getenv("ODDSPAPI_SPORT_ID", "13"))  # MLB / baseball
BOOKMAKER = os.getenv("ODDSPAPI_BOOKMAKER", "betano.bet.br")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))

NORMAL = NormalDist()


def env_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def api_get(endpoint: str, params: dict) -> dict | list:
    api_key = env_required("ODDSPAPI_API_KEY")
    merged = {"apiKey": api_key, **params}
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    response = requests.get(url, params=merged, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def report_date() -> datetime:
    raw = os.getenv("REPORT_DATE")
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def load_market_catalog() -> dict[int, dict]:
    data = api_get("markets", {"sportId": SPORT_ID})
    catalog = {}
    for row in data:
        market_id = int(row["marketId"])
        outcomes = {}
        for out in row.get("outcomes", []) or []:
            try:
                outcomes[str(out.get("outcomeId"))] = out.get("outcomeName")
            except Exception:
                pass
        catalog[market_id] = {
            "name": row.get("marketName"),
            "handicap": row.get("handicap"),
            "outcomes": outcomes,
        }
    return catalog


def fetch_fixtures(day: datetime) -> list[dict]:
    start = day.strftime("%Y-%m-%d")
    end = (day + timedelta(days=1)).strftime("%Y-%m-%d")
    data = api_get("fixtures", {"sportId": SPORT_ID, "from": start, "to": end})
    fixtures = []
    for f in data:
        if not f.get("hasOdds"):
            continue
        if str(f.get("tournamentName", "")).upper() != "MLB":
            continue
        if str(f.get("statusName", "")).lower() not in {"pre-game", "not started", "scheduled"}:
            continue
        fixtures.append(f)
    fixtures.sort(key=lambda x: x.get("startTime", ""))
    return fixtures


def fetch_odds(fixture_id: str) -> dict:
    data = api_get("odds", {"fixtureId": fixture_id, "bookmakers": BOOKMAKER})
    return data.get("bookmakerOdds", {}).get(BOOKMAKER, {})


def try_float(x):
    try:
        return float(x)
    except Exception:
        return None


def extract_price(player_node) -> float | None:
    if isinstance(player_node, dict):
        price = player_node.get("price")
        return try_float(price)
    if isinstance(player_node, list) and player_node:
        price = player_node[-1].get("price")
        return try_float(price)
    return None


def outcome_name(catalog: dict[int, dict], market_id: str, outcome_id: str) -> str:
    try:
        mid = int(market_id)
    except Exception:
        return ""
    return str(catalog.get(mid, {}).get("outcomes", {}).get(str(outcome_id), "")).lower()


def market_info(catalog: dict[int, dict], market_id: str) -> tuple[str, float | None]:
    try:
        mid = int(market_id)
    except Exception:
        return "", None
    row = catalog.get(mid, {})
    return str(row.get("name", "")), try_float(row.get("handicap"))


def choose_best_total(book_markets: dict, catalog: dict[int, dict], target_phrase: str) -> dict | None:
    candidates = []
    for mid, market in (book_markets or {}).items():
        name, handicap = market_info(catalog, mid)
        if name != target_phrase:
            continue
        outcomes = market.get("outcomes", {}) or {}
        over_price = None
        under_price = None
        for oid, onode in outcomes.items():
            label = outcome_name(catalog, mid, oid)
            player_node = (onode.get("players") or {}).get("0")
            price = extract_price(player_node)
            if price is None:
                continue
            if "over" in label:
                over_price = price
            elif "under" in label:
                under_price = price
        if over_price and under_price and handicap is not None:
            hold = (1 / over_price) + (1 / under_price) - 1
            candidates.append({
                "line": handicap,
                "over": over_price,
                "under": under_price,
                "hold": hold,
            })
    if not candidates:
        return None
    candidates.sort(key=lambda x: (abs(x["hold"]), abs(x["line"] - round(x["line"])) ))
    return candidates[0]


def choose_moneyline(book_markets: dict, catalog: dict[int, dict], target_phrase: str) -> dict | None:
    for mid, market in (book_markets or {}).items():
        name, _ = market_info(catalog, mid)
        if name != target_phrase:
            continue
        outcomes = market.get("outcomes", {}) or {}
        prices = []
        for oid, onode in outcomes.items():
            player_node = (onode.get("players") or {}).get("0")
            price = extract_price(player_node)
            if price is None:
                continue
            prices.append((str(oid), price))
        if len(prices) == 2:
            prices.sort(key=lambda x: int(x[0]))
            return {"away": prices[0][1], "home": prices[1][1]}
    return None


def choose_handicap(book_markets: dict, catalog: dict[int, dict], target_phrase: str, wanted_line: float) -> dict | None:
    candidates = []
    for mid, market in (book_markets or {}).items():
        name, handicap = market_info(catalog, mid)
        if name != target_phrase or handicap is None:
            continue
        if abs(abs(handicap) - wanted_line) > 1e-9:
            continue
        outcomes = market.get("outcomes", {}) or {}
        rows = []
        for oid, onode in outcomes.items():
            label = outcome_name(catalog, mid, oid)
            player_node = (onode.get("players") or {}).get("0")
            price = extract_price(player_node)
            if price is None:
                continue
            rows.append((label, price, handicap))
        if len(rows) != 2:
            continue

        home_minus = None
        away_plus = None
        for label, price, hc in rows:
            if any(k in label for k in ["home", "2"]):
                if hc < 0:
                    home_minus = price
                elif hc > 0:
                    away_plus = price
            if any(k in label for k in ["away", "1"]):
                if hc > 0:
                    away_plus = price
                elif hc < 0:
                    home_minus = price

        if home_minus is None or away_plus is None:
            if handicap < 0:
                home_minus = rows[0][1]
                away_plus = rows[1][1]
            else:
                away_plus = rows[0][1]
                home_minus = rows[1][1]

        if home_minus and away_plus:
            hold = (1 / home_minus) + (1 / away_plus) - 1
            candidates.append({
                "line": wanted_line,
                "home_minus": home_minus,
                "away_plus": away_plus,
                "hold": hold,
            })
    if not candidates:
        return None
    candidates.sort(key=lambda x: x["hold"])
    return candidates[0]


def win_prob_from_odds(home_odds: float, away_odds: float) -> float:
    ih = 1 / home_odds
    ia = 1 / away_odds
    return ih / (ih + ia)


def lambdas_from_total_and_ml(total_line: float, home_prob: float) -> tuple[float, float]:
    p = min(max(home_prob, 0.01), 0.99)
    z = NORMAL.inv_cdf(p)
    mean_diff = z * math.sqrt(max(total_line, 0.5))
    home = max(0.2, (total_line + mean_diff) / 2)
    away = max(0.2, total_line - home)
    return round(home, 3), round(away, 3)


def starter_name(side: str, fixture: dict) -> str:
    keys = [
        f"{side}ProbablePitcherName",
        f"{side}PitcherName",
        f"{side}StartingPitcherName",
    ]
    for key in keys:
        value = fixture.get(key)
        if value:
            return str(value)
    return "TBD"


def venue_name(fixture: dict) -> str:
    for key in ["venueName", "stadiumName", "groundName"]:
        if fixture.get(key):
            return str(fixture[key])
    return "N/A"


def build_game(fixture: dict, book: dict, catalog: dict[int, dict]) -> dict | None:
    markets = book.get("markets") or {}

    fg_ml = choose_moneyline(markets, catalog, "Winner (incl. extra innings)")
    fg_total = choose_best_total(markets, catalog, "Over Under (incl. extra innings)")
    fg_rl = choose_handicap(markets, catalog, "Handicap (incl. extra innings)", 1.5)

    f5_ml = choose_moneyline(markets, catalog, "Winner 1st 5 innings")
    f5_total = choose_best_total(markets, catalog, "Over Under 1st 5 innings")
    f5_rl = choose_handicap(markets, catalog, "Handicap 1st 5 innings", 0.5)

    if not fg_ml or not fg_total:
        return None

    home_prob = win_prob_from_odds(fg_ml["home"], fg_ml["away"])
    lambda_home_9, lambda_away_9 = lambdas_from_total_and_ml(fg_total["line"], home_prob)

    if f5_ml and f5_total:
        home_prob_5 = win_prob_from_odds(f5_ml["home"], f5_ml["away"])
        lambda_home_5, lambda_away_5 = lambdas_from_total_and_ml(f5_total["line"], home_prob_5)
    else:
        lambda_home_5 = round(lambda_home_9 * 5 / 9, 3)
        lambda_away_5 = round(lambda_away_9 * 5 / 9, 3)

    away_team = fixture.get("participant1Name") or fixture.get("awayTeamName") or "Away Team"
    home_team = fixture.get("participant2Name") or fixture.get("homeTeamName") or "Home Team"

    game = {
        "away_team": away_team,
        "home_team": home_team,
        "venue": venue_name(fixture),
        "away_starter": starter_name("away", fixture),
        "home_starter": starter_name("home", fixture),
        "starters_confirmed": False,
        "lineup_status": "projected",
        "weather_note": "Weather feed not connected in v1 live build.",
        "weather_penalty": 0.5,
        "bullpen_note": "Bullpen feed not connected in v1 live build; neutral bullpen penalty applied.",
        "bullpen_penalty": 0.0,
        "lineup_penalty": 0.5,
        "away_starter_limited_sample": False,
        "home_starter_limited_sample": False,
        "lambda_away_5": lambda_away_5,
        "lambda_home_5": lambda_home_5,
        "lambda_away_9": lambda_away_9,
        "lambda_home_9": lambda_home_9,
        "odds": {
            "fg_ml": fg_ml,
            "fg_total": {
                "line": fg_total["line"],
                "over": fg_total["over"],
                "under": fg_total["under"],
            },
        },
        "source_meta": {
            "fixture_id": fixture.get("fixtureId"),
            "bookmaker": BOOKMAKER,
            "start_time": fixture.get("startTime"),
        },
    }

    if fg_rl:
        game["odds"]["fg_rl"] = fg_rl
    if f5_ml:
        game["odds"]["f5_ml"] = f5_ml
    if f5_total:
        game["odds"]["f5_total"] = {
            "line": f5_total["line"],
            "over": f5_total["over"],
            "under": f5_total["under"],
        }
    if f5_rl:
        game["odds"]["f5_rl"] = f5_rl

    return game


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a live MLB slate from OddsPapi/Betano odds.")
    parser.add_argument("--output", default="mlb_automation/live_slate.json")
    args = parser.parse_args()

    day = report_date()
    catalog = load_market_catalog()
    fixtures = fetch_fixtures(day)

    games = []
    skipped = []
    for fixture in fixtures:
        fixture_id = fixture.get("fixtureId")
        if not fixture_id:
            continue
        try:
            book = fetch_odds(fixture_id)
            game = build_game(fixture, book, catalog)
            if game:
                games.append(game)
            else:
                skipped.append({
                    "fixtureId": fixture_id,
                    "reason": "required markets missing",
                    "away": fixture.get("participant1Name"),
                    "home": fixture.get("participant2Name"),
                })
        except Exception as exc:
            skipped.append({
                "fixtureId": fixture_id,
                "reason": str(exc),
                "away": fixture.get("participant1Name"),
                "home": fixture.get("participant2Name"),
            })

    payload = {
        "date": day.strftime("%Y-%m-%d"),
        "source": {
            "provider": "OddsPapi",
            "bookmaker": BOOKMAKER,
            "sportId": SPORT_ID,
        },
        "games": games,
        "skipped": skipped,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(games)} games to {out}")
    if skipped:
        print(f"Skipped {len(skipped)} fixtures")


if __name__ == "__main__":
    main()
