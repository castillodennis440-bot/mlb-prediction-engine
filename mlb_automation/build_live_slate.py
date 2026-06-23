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
BOOKMAKER = os.getenv("ODDSPAPI_BOOKMAKER", "pinnacle")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

NORMAL = NormalDist()

TEAM_PARKS = {
    "Arizona Diamondbacks": {"venue": "Chase Field", "lat": 33.4453, "lon": -112.0667},
    "Atlanta Braves": {"venue": "Truist Park", "lat": 33.8907, "lon": -84.4677},
    "Baltimore Orioles": {"venue": "Oriole Park at Camden Yards", "lat": 39.2838, "lon": -76.6217},
    "Boston Red Sox": {"venue": "Fenway Park", "lat": 42.3467, "lon": -71.0972},
    "Chicago Cubs": {"venue": "Wrigley Field", "lat": 41.9484, "lon": -87.6553},
    "Chicago White Sox": {"venue": "Guaranteed Rate Field", "lat": 41.8299, "lon": -87.6338},
    "Cincinnati Reds": {"venue": "Great American Ball Park", "lat": 39.0979, "lon": -84.5073},
    "Cleveland Guardians": {"venue": "Progressive Field", "lat": 41.4962, "lon": -81.6852},
    "Colorado Rockies": {"venue": "Coors Field", "lat": 39.7561, "lon": -104.9942},
    "Detroit Tigers": {"venue": "Comerica Park", "lat": 42.3390, "lon": -83.0485},
    "Houston Astros": {"venue": "Daikin Park", "lat": 29.7573, "lon": -95.3555},
    "Kansas City Royals": {"venue": "Kauffman Stadium", "lat": 39.0517, "lon": -94.4803},
    "Los Angeles Angels": {"venue": "Angel Stadium", "lat": 33.8003, "lon": -117.8827},
    "Los Angeles Dodgers": {"venue": "Dodger Stadium", "lat": 34.0739, "lon": -118.2400},
    "Miami Marlins": {"venue": "loanDepot park", "lat": 25.7781, "lon": -80.2197},
    "Milwaukee Brewers": {"venue": "American Family Field", "lat": 43.0280, "lon": -87.9712},
    "Minnesota Twins": {"venue": "Target Field", "lat": 44.9817, "lon": -93.2776},
    "New York Mets": {"venue": "Citi Field", "lat": 40.7571, "lon": -73.8458},
    "New York Yankees": {"venue": "Yankee Stadium", "lat": 40.8296, "lon": -73.9262},
    "Athletics": {"venue": "Sutter Health Park", "lat": 38.5800, "lon": -121.5136},
    "Philadelphia Phillies": {"venue": "Citizens Bank Park", "lat": 39.9061, "lon": -75.1665},
    "Pittsburgh Pirates": {"venue": "PNC Park", "lat": 40.4469, "lon": -80.0057},
    "San Diego Padres": {"venue": "Petco Park", "lat": 32.7073, "lon": -117.1573},
    "San Francisco Giants": {"venue": "Oracle Park", "lat": 37.7786, "lon": -122.3893},
    "Seattle Mariners": {"venue": "T-Mobile Park", "lat": 47.5914, "lon": -122.3325},
    "St. Louis Cardinals": {"venue": "Busch Stadium", "lat": 38.6226, "lon": -90.1928},
    "Tampa Bay Rays": {"venue": "George M. Steinbrenner Field", "lat": 27.9800, "lon": -82.5070},
    "Texas Rangers": {"venue": "Globe Life Field", "lat": 32.7473, "lon": -97.0842},
    "Toronto Blue Jays": {"venue": "Rogers Centre", "lat": 43.6414, "lon": -79.3894},
    "Washington Nationals": {"venue": "Nationals Park", "lat": 38.8729, "lon": -77.0074},
}

TEAM_ALIASES = {
    "A's": "Athletics",
    "Oakland Athletics": "Athletics",
    "Athletics": "Athletics",
}


class ApiError(RuntimeError):
    pass


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
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ApiError(f"{response.status_code} {response.reason}") from exc
    return response.json()


def safe_get(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def normalize_team(name: str) -> str:
    if not name:
        return name
    return TEAM_ALIASES.get(name, name)


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


def fetch_mlb_schedule_map(day: datetime) -> dict:
    payload = safe_get(
        MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "date": day.strftime("%Y-%m-%d"),
            "hydrate": "probablePitcher,venue",
        },
    )
    mapping = {}
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            away = normalize_team(game.get("teams", {}).get("away", {}).get("team", {}).get("name", ""))
            home = normalize_team(game.get("teams", {}).get("home", {}).get("team", {}).get("name", ""))
            if not away or not home:
                continue
            mapping[(away, home)] = {
                "game_pk": game.get("gamePk"),
                "start_time": game.get("gameDate"),
                "venue": game.get("venue", {}).get("name"),
                "away_team_id": game.get("teams", {}).get("away", {}).get("team", {}).get("id"),
                "home_team_id": game.get("teams", {}).get("home", {}).get("team", {}).get("id"),
                "away_starter": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName"),
                "home_starter": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName"),
            }
    return mapping


def fetch_recent_team_games(team_id: int, day: datetime) -> list[int]:
    payload = safe_get(
        MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": (day - timedelta(days=3)).strftime("%Y-%m-%d"),
            "endDate": (day - timedelta(days=1)).strftime("%Y-%m-%d"),
        },
    )
    game_pks = []
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            if str(game.get("status", {}).get("abstractGameState", "")).lower() != "final":
                continue
            game_pk = game.get("gamePk")
            if game_pk:
                game_pks.append(int(game_pk))
    return game_pks


def bullpen_summary(team_id: int, team_name: str, day: datetime, cache: dict) -> tuple[str, float]:
    if team_id in cache:
        return cache[team_id]

    try:
        game_pks = fetch_recent_team_games(team_id, day)
    except Exception:
        note = "Bullpen usage feed unavailable; neutral bullpen penalty applied."
        cache[team_id] = (note, 0.25)
        return cache[team_id]

    total_pitches = 0
    appearances = 0
    innings = 0.0

    for game_pk in game_pks:
        try:
            box = safe_get(MLB_BOXSCORE_URL.format(game_pk=game_pk), {})
        except Exception:
            continue

        home_team = box.get("teams", {}).get("home", {}).get("team", {}).get("id")
        away_team = box.get("teams", {}).get("away", {}).get("team", {}).get("id")
        if team_id == home_team:
            team_box = box.get("teams", {}).get("home", {})
        elif team_id == away_team:
            team_box = box.get("teams", {}).get("away", {})
        else:
            continue

        players = team_box.get("players", {}) or {}
        bullpen_ids = team_box.get("bullpen", []) or []
        for pid in bullpen_ids:
            player = players.get(f"ID{pid}") or {}
            pstats = player.get("stats", {}).get("pitching", {}) or {}
            pitches = int(pstats.get("numberOfPitches") or 0)
            ip_raw = str(pstats.get("inningsPitched") or "0")
            try:
                ip = float(ip_raw)
            except Exception:
                ip = 0.0
            if pitches > 0 or ip > 0:
                total_pitches += pitches
                innings += ip
                appearances += 1

    penalty = 0.0
    color = "GREEN"
    if total_pitches >= 90 or appearances >= 7:
        penalty = 1.0
        color = "RED"
    elif total_pitches >= 55 or appearances >= 4:
        penalty = 0.5
        color = "YELLOW"

    if appearances == 0:
        note = f"Bullpen {color}: no recent relief workload captured for {team_name}."
    else:
        note = f"Bullpen {color}: {appearances} relief apps, {total_pitches} pitches, {innings:.1f} IP over last 3 days."
    cache[team_id] = (note, penalty)
    return cache[team_id]


def fetch_weather(home_team: str, start_time: str) -> tuple[str, float, float]:
    park = TEAM_PARKS.get(home_team)
    if not park or not start_time:
        return ("Weather feed unavailable; neutral weather penalty applied.", 0.25, 0.0)

    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    try:
        payload = safe_get(
            OPEN_METEO_URL,
            {
                "latitude": park["lat"],
                "longitude": park["lon"],
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m",
                "start_date": start_dt.strftime("%Y-%m-%d"),
                "end_date": start_dt.strftime("%Y-%m-%d"),
                "timezone": "UTC",
            },
        )
        hours = payload.get("hourly", {})
        times = hours.get("time", [])
        target = start_dt.strftime("%Y-%m-%dT%H:00")
        if target in times:
            idx = times.index(target)
        elif times:
            idx = 0
        else:
            raise RuntimeError("missing hourly weather")

        temp = float(hours.get("temperature_2m", [None])[idx])
        precip = float(hours.get("precipitation_probability", [0])[idx] or 0)
        wind = float(hours.get("wind_speed_10m", [0])[idx] or 0)
    except Exception:
        return ("Weather feed unavailable; neutral weather penalty applied.", 0.25, 0.0)

    penalty = 0.0
    total_adjust = 0.0
    if temp >= 30:
        total_adjust += 0.35
    elif temp >= 27:
        total_adjust += 0.20
    elif temp <= 13:
        total_adjust -= 0.20
    elif temp <= 8:
        total_adjust -= 0.35

    if wind >= 24:
        penalty += 0.75
        total_adjust += 0.10
    elif wind >= 16:
        penalty += 0.40

    if precip >= 50:
        penalty += 0.60
    elif precip >= 25:
        penalty += 0.25

    note = f"Weather: {temp:.0f}°C, wind {wind:.0f} km/h, precip {precip:.0f}% at first pitch."
    return note, round(penalty, 2), round(total_adjust, 2)


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
            candidates.append({"line": handicap, "over": over_price, "under": under_price, "hold": hold})
    if not candidates:
        return None
    candidates.sort(key=lambda x: abs(x["hold"]))
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
            candidates.append({"line": wanted_line, "home_minus": home_minus, "away_plus": away_plus, "hold": hold})
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


def apply_total_adjustment(home_lambda: float, away_lambda: float, total_delta: float) -> tuple[float, float]:
    total = max(home_lambda + away_lambda, 0.4)
    home_share = home_lambda / total
    away_share = away_lambda / total
    return round(home_lambda + total_delta * home_share, 3), round(away_lambda + total_delta * away_share, 3)


def lineup_status_and_penalty(start_time: str | None, now_utc: datetime) -> tuple[str, float]:
    if not start_time:
        return "projected", 0.50
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except Exception:
        return "projected", 0.50
    mins = (start_dt - now_utc).total_seconds() / 60.0
    if mins <= 120:
        return "projected-near-lock", 0.25
    return "projected", 0.50


def build_game(
    fixture: dict,
    book: dict,
    catalog: dict[int, dict],
    schedule_map: dict,
    bullpen_cache: dict,
    now_utc: datetime,
) -> dict | None:
    away_team = normalize_team(fixture.get("participant1Name") or fixture.get("awayTeamName") or "Away Team")
    home_team = normalize_team(fixture.get("participant2Name") or fixture.get("homeTeamName") or "Home Team")
    sched = schedule_map.get((away_team, home_team), {})

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

    venue = sched.get("venue") or TEAM_PARKS.get(home_team, {}).get("venue") or "N/A"
    start_time = sched.get("start_time") or fixture.get("startTime")
    weather_note, weather_penalty, total_adjust = fetch_weather(home_team, start_time)
    lambda_home_9, lambda_away_9 = apply_total_adjustment(lambda_home_9, lambda_away_9, total_adjust)
    lambda_home_5, lambda_away_5 = apply_total_adjustment(lambda_home_5, lambda_away_5, total_adjust * 5 / 9)

    away_team_id = sched.get("away_team_id")
    home_team_id = sched.get("home_team_id")
    away_bullpen_note, away_bullpen_penalty = bullpen_summary(away_team_id, away_team, now_utc, bullpen_cache) if away_team_id else ("Away bullpen data unavailable.", 0.25)
    home_bullpen_note, home_bullpen_penalty = bullpen_summary(home_team_id, home_team, now_utc, bullpen_cache) if home_team_id else ("Home bullpen data unavailable.", 0.25)
    bullpen_penalty = round(max(away_bullpen_penalty, home_bullpen_penalty), 2)
    bullpen_note = f"{away_team}: {away_bullpen_note} | {home_team}: {home_bullpen_note}"

    lineup_status, lineup_penalty = lineup_status_and_penalty(start_time, now_utc)
    away_starter = sched.get("away_starter") or "TBD"
    home_starter = sched.get("home_starter") or "TBD"
    starters_confirmed = away_starter != "TBD" and home_starter != "TBD"

    game = {
        "away_team": away_team,
        "home_team": home_team,
        "venue": venue,
        "away_starter": away_starter,
        "home_starter": home_starter,
        "starters_confirmed": starters_confirmed,
        "lineup_status": lineup_status,
        "weather_note": weather_note,
        "weather_penalty": weather_penalty,
        "bullpen_note": bullpen_note,
        "bullpen_penalty": bullpen_penalty,
        "lineup_penalty": lineup_penalty,
        "away_starter_limited_sample": False,
        "home_starter_limited_sample": False,
        "lambda_away_5": lambda_away_5,
        "lambda_home_5": lambda_home_5,
        "lambda_away_9": lambda_away_9,
        "lambda_home_9": lambda_home_9,
        "odds": {
            "fg_ml": fg_ml,
            "fg_total": {"line": fg_total["line"], "over": fg_total["over"], "under": fg_total["under"]},
        },
        "source_meta": {
            "fixture_id": fixture.get("fixtureId"),
            "bookmaker": BOOKMAKER,
            "start_time": start_time,
        },
    }

    if fg_rl:
        game["odds"]["fg_rl"] = fg_rl
    if f5_ml:
        game["odds"]["f5_ml"] = f5_ml
    if f5_total:
        game["odds"]["f5_total"] = {"line": f5_total["line"], "over": f5_total["over"], "under": f5_total["under"]}
    if f5_rl:
        game["odds"]["f5_rl"] = f5_rl

    return game


def sanitize_error(exc: Exception) -> str:
    msg = str(exc)
    if "403" in msg:
        return "403 Forbidden from odds endpoint"
    if "429" in msg:
        return "429 Too Many Requests from odds endpoint"
    if " for url:" in msg:
        return msg.split(" for url:")[0]
    return msg


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a live MLB slate from OddsPapi odds.")
    parser.add_argument("--output", default="mlb_automation/live_slate.json")
    args = parser.parse_args()

    now_utc = report_date()
    catalog = load_market_catalog()
    fixtures = fetch_fixtures(now_utc)
    schedule_map = fetch_mlb_schedule_map(now_utc)
    bullpen_cache = {}

    games = []
    skipped = []
    for fixture in fixtures:
        fixture_id = fixture.get("fixtureId")
        if not fixture_id:
            continue
        try:
            book = fetch_odds(fixture_id)
            game = build_game(fixture, book, catalog, schedule_map, bullpen_cache, now_utc)
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
                "reason": sanitize_error(exc),
                "away": fixture.get("participant1Name"),
                "home": fixture.get("participant2Name"),
            })

    payload = {
        "date": now_utc.strftime("%Y-%m-%d"),
        "generated_at_utc": now_utc.isoformat(),
        "source": {"provider": "OddsPapi", "bookmaker": BOOKMAKER, "sportId": SPORT_ID},
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
