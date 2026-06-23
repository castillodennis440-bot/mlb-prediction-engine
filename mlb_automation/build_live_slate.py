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
MLB_PEOPLE_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}/stats"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
LEAGUE_RUNS_PER_TEAM = 4.35

NORMAL = NormalDist()

TEAM_PARKS = {
    "Arizona Diamondbacks": {"venue": "Chase Field", "lat": 33.4453, "lon": -112.0667},
    "Atlanta Braves": {"venue": "Truist Park", "lat": 33.8907, "lon": -84.4677},
    "Baltimore Orioles": {"venue": "Oriole Park at Camden Yards", "lat": 39.2838, "lon": -76.6217},
    "Boston Red Sox": {"venue": "Fenway Park", "lat": 42.3467, "lon": -71.0972},
    "Chicago Cubs": {"venue": "Wrigley Field", "lat": 41.9484, "lon": -87.6553},
    "Chicago White Sox": {"venue": "Rate Field", "lat": 41.8299, "lon": -87.6338},
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


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def parse_innings_pitched(value) -> float:
    if value is None:
        return 0.0
    s = str(value)
    if not s:
        return 0.0
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
    frac_val = 0.0
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
            away_team = normalize_team(game.get("teams", {}).get("away", {}).get("team", {}).get("name", ""))
            home_team = normalize_team(game.get("teams", {}).get("home", {}).get("team", {}).get("name", ""))
            if not away_team or not home_team:
                continue
            mapping[(away_team, home_team)] = {
                "game_pk": game.get("gamePk"),
                "start_time": game.get("gameDate"),
                "venue": game.get("venue", {}).get("name"),
                "away_team_id": game.get("teams", {}).get("away", {}).get("team", {}).get("id"),
                "home_team_id": game.get("teams", {}).get("home", {}).get("team", {}).get("id"),
                "away_starter": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName"),
                "home_starter": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName"),
                "away_starter_id": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id"),
                "home_starter_id": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id"),
            }
    return mapping


def fetch_recent_team_games(team_id: int, day: datetime) -> list[dict]:
    payload = safe_get(
        MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": (day - timedelta(days=21)).strftime("%Y-%m-%d"),
            "endDate": (day - timedelta(days=1)).strftime("%Y-%m-%d"),
        },
    )
    games = []
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            if str(game.get("status", {}).get("abstractGameState", "")).lower() != "final":
                continue
            game_pk = game.get("gamePk")
            away_id = game.get("teams", {}).get("away", {}).get("team", {}).get("id")
            home_id = game.get("teams", {}).get("home", {}).get("team", {}).get("id")
            away_score = game.get("teams", {}).get("away", {}).get("score")
            home_score = game.get("teams", {}).get("home", {}).get("score")
            if team_id == away_id:
                runs_for = away_score
                runs_against = home_score
                location = "away"
            elif team_id == home_id:
                runs_for = home_score
                runs_against = away_score
                location = "home"
            else:
                continue
            if runs_for is None or runs_against is None:
                continue
            games.append({
                "game_pk": game_pk,
                "runs_for": int(runs_for),
                "runs_against": int(runs_against),
                "won": int(runs_for) > int(runs_against),
                "location": location,
            })
    games.sort(key=lambda x: x["game_pk"], reverse=True)
    return games[:10]


def recent_form_summary(team_id: int, team_name: str, day: datetime, cache: dict) -> dict:
    if team_id in cache:
        return cache[team_id]

    try:
        games = fetch_recent_team_games(team_id, day)
    except Exception:
        result = {
            "offense_factor": 1.0,
            "defense_factor": 1.0,
            "form_factor": 1.0,
            "note": f"{team_name}: recent-form feed unavailable.",
        }
        cache[team_id] = result
        return result

    if not games:
        result = {
            "offense_factor": 1.0,
            "defense_factor": 1.0,
            "form_factor": 1.0,
            "note": f"{team_name}: no recent final games found.",
        }
        cache[team_id] = result
        return result

    n = len(games)
    wins = sum(1 for g in games if g["won"])
    rs_pg = sum(g["runs_for"] for g in games) / n
    ra_pg = sum(g["runs_against"] for g in games) / n
    win_pct = wins / n
    offense_factor = clamp(rs_pg / LEAGUE_RUNS_PER_TEAM, 0.78, 1.25)
    defense_factor = clamp(ra_pg / LEAGUE_RUNS_PER_TEAM, 0.78, 1.25)
    form_factor = clamp(1.0 + (win_pct - 0.5) * 0.20, 0.92, 1.08)
    result = {
        "offense_factor": round(offense_factor, 3),
        "defense_factor": round(defense_factor, 3),
        "form_factor": round(form_factor, 3),
        "note": f"{team_name}: {wins}-{n-wins} last {n}, RS/G {rs_pg:.2f}, RA/G {ra_pg:.2f}.",
    }
    cache[team_id] = result
    return result


def fetch_pitcher_rating(person_id: int | None, pitcher_name: str, day: datetime, cache: dict) -> dict:
    if not person_id:
        return {
            "factor": 1.0,
            "limited": True,
            "note": f"{pitcher_name or 'TBD'}: starter stats unavailable.",
        }
    if person_id in cache:
        return cache[person_id]

    try:
        payload = safe_get(
            MLB_PEOPLE_STATS_URL.format(person_id=person_id),
            {"stats": "season", "group": "pitching", "season": day.year},
        )
        splits = ((payload.get("stats") or [{}])[0].get("splits") or [])
        stat = (splits[0].get("stat") if splits else {}) or {}
    except Exception:
        result = {
            "factor": 1.0,
            "limited": True,
            "note": f"{pitcher_name}: starter stats request failed.",
        }
        cache[person_id] = result
        return result

    games_started = int(stat.get("gamesStarted") or 0)
    innings_pitched = parse_innings_pitched(stat.get("inningsPitched"))
    era = try_float(stat.get("era")) or 4.20
    whip = try_float(stat.get("whip")) or 1.30
    strikeouts = int(stat.get("strikeOuts") or 0)
    walks = int(stat.get("baseOnBalls") or 0)
    k9 = (strikeouts / innings_pitched * 9.0) if innings_pitched > 0 else 8.5
    bb9 = (walks / innings_pitched * 9.0) if innings_pitched > 0 else 3.2

    era_factor = clamp(era / 4.20, 0.72, 1.35)
    whip_factor = clamp(whip / 1.30, 0.78, 1.25)
    k_factor = clamp(8.5 / max(k9, 4.5), 0.82, 1.20)
    bb_factor = clamp(bb9 / 3.2, 0.80, 1.20)
    base_factor = 0.45 * era_factor + 0.25 * whip_factor + 0.20 * k_factor + 0.10 * bb_factor

    limited = games_started < 4 or innings_pitched < 20
    if limited:
        factor = 0.65 * base_factor + 0.35 * 1.0
    else:
        factor = base_factor

    result = {
        "factor": round(clamp(factor, 0.75, 1.25), 3),
        "limited": limited,
        "note": (
            f"{pitcher_name}: ERA {era:.2f}, WHIP {whip:.2f}, K/9 {k9:.1f}, BB/9 {bb9:.1f}, "
            f"GS {games_started}, IP {innings_pitched:.1f}"
            + (" (limited sample)" if limited else "")
        ),
    }
    cache[person_id] = result
    return result


def bullpen_summary(team_id: int, team_name: str, day: datetime, team_cache: dict, boxscore_cache: dict) -> tuple[str, float]:
    if team_id in team_cache:
        return team_cache[team_id]

    try:
        recent_games = fetch_recent_team_games(team_id, day)
    except Exception:
        note = "Bullpen usage feed unavailable; neutral bullpen penalty applied."
        team_cache[team_id] = (note, 0.25)
        return team_cache[team_id]

    total_pitches = 0
    appearances = 0
    innings = 0.0

    for row in recent_games[:3]:
        game_pk = row["game_pk"]
        if game_pk not in boxscore_cache:
            try:
                boxscore_cache[game_pk] = safe_get(MLB_BOXSCORE_URL.format(game_pk=game_pk), {})
            except Exception:
                boxscore_cache[game_pk] = None
        box = boxscore_cache.get(game_pk)
        if not box:
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
            ip = parse_innings_pitched(pstats.get("inningsPitched"))
            if pitches > 0 or ip > 0:
                total_pitches += pitches
                innings += ip
                appearances += 1

    penalty = 0.0
    color = "GREEN"
    if total_pitches >= 95 or appearances >= 7:
        penalty = 1.0
        color = "RED"
    elif total_pitches >= 55 or appearances >= 4:
        penalty = 0.5
        color = "YELLOW"
    elif total_pitches > 0:
        penalty = 0.2

    if appearances == 0:
        note = f"Bullpen {color}: no recent relief workload captured for {team_name}."
    else:
        note = f"Bullpen {color}: {appearances} relief apps, {total_pitches} pitches, {innings:.1f} IP in last 3 games."
    team_cache[team_id] = (note, penalty)
    return team_cache[team_id]


def fetch_weather(home_team: str, start_time: str | None) -> tuple[str, float, float]:
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
        idx = times.index(target) if target in times else 0
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
    p = clamp(home_prob, 0.01, 0.99)
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


def bullpen_boost(opponent_bullpen_penalty: float) -> float:
    if opponent_bullpen_penalty >= 1.0:
        return 0.35
    if opponent_bullpen_penalty >= 0.5:
        return 0.20
    if opponent_bullpen_penalty > 0:
        return 0.08
    return 0.0


def build_custom_lambdas(home_form: dict, away_form: dict, home_pitcher: dict, away_pitcher: dict,
                         home_bullpen_penalty: float, away_bullpen_penalty: float) -> tuple[float, float, float, float]:
    home_model_9 = LEAGUE_RUNS_PER_TEAM * home_form["offense_factor"] * away_form["defense_factor"] * away_pitcher["factor"] * home_form["form_factor"] * 1.04
    away_model_9 = LEAGUE_RUNS_PER_TEAM * away_form["offense_factor"] * home_form["defense_factor"] * home_pitcher["factor"] * away_form["form_factor"] * 0.96
    home_model_9 += bullpen_boost(away_bullpen_penalty)
    away_model_9 += bullpen_boost(home_bullpen_penalty)

    home_model_5 = (LEAGUE_RUNS_PER_TEAM * 5.0 / 9.0) * home_form["offense_factor"] * away_form["defense_factor"] * (away_pitcher["factor"] ** 1.10) * home_form["form_factor"] * 1.03
    away_model_5 = (LEAGUE_RUNS_PER_TEAM * 5.0 / 9.0) * away_form["offense_factor"] * home_form["defense_factor"] * (home_pitcher["factor"] ** 1.10) * away_form["form_factor"] * 0.97

    return (
        round(clamp(home_model_9, 2.2, 7.0), 3),
        round(clamp(away_model_9, 2.2, 7.0), 3),
        round(clamp(home_model_5, 1.1, 4.5), 3),
        round(clamp(away_model_5, 1.1, 4.5), 3),
    )


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
    bullpen_team_cache: dict,
    bullpen_boxscore_cache: dict,
    form_cache: dict,
    pitcher_cache: dict,
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

    market_home_9, market_away_9 = lambdas_from_total_and_ml(fg_total["line"], win_prob_from_odds(fg_ml["home"], fg_ml["away"]))
    if f5_ml and f5_total:
        market_home_5, market_away_5 = lambdas_from_total_and_ml(f5_total["line"], win_prob_from_odds(f5_ml["home"], f5_ml["away"]))
    else:
        market_home_5 = round(market_home_9 * 5 / 9, 3)
        market_away_5 = round(market_away_9 * 5 / 9, 3)

    venue = sched.get("venue") or TEAM_PARKS.get(home_team, {}).get("venue") or "N/A"
    start_time = sched.get("start_time") or fixture.get("startTime")

    away_team_id = sched.get("away_team_id")
    home_team_id = sched.get("home_team_id")
    away_form = recent_form_summary(away_team_id, away_team, now_utc, form_cache) if away_team_id else {"offense_factor": 1.0, "defense_factor": 1.0, "form_factor": 1.0, "note": f"{away_team}: recent-form feed unavailable."}
    home_form = recent_form_summary(home_team_id, home_team, now_utc, form_cache) if home_team_id else {"offense_factor": 1.0, "defense_factor": 1.0, "form_factor": 1.0, "note": f"{home_team}: recent-form feed unavailable."}

    away_starter = sched.get("away_starter") or "TBD"
    home_starter = sched.get("home_starter") or "TBD"
    away_starter_id = sched.get("away_starter_id")
    home_starter_id = sched.get("home_starter_id")
    away_pitcher = fetch_pitcher_rating(away_starter_id, away_starter, now_utc, pitcher_cache)
    home_pitcher = fetch_pitcher_rating(home_starter_id, home_starter, now_utc, pitcher_cache)

    away_bullpen_note, away_bullpen_penalty = bullpen_summary(away_team_id, away_team, now_utc, bullpen_team_cache, bullpen_boxscore_cache) if away_team_id else ("Away bullpen data unavailable.", 0.25)
    home_bullpen_note, home_bullpen_penalty = bullpen_summary(home_team_id, home_team, now_utc, bullpen_team_cache, bullpen_boxscore_cache) if home_team_id else ("Home bullpen data unavailable.", 0.25)
    bullpen_penalty = round(max(away_bullpen_penalty, home_bullpen_penalty), 2)
    bullpen_note = f"{away_team}: {away_bullpen_note} | {home_team}: {home_bullpen_note}"

    model_home_9, model_away_9, model_home_5, model_away_5 = build_custom_lambdas(home_form, away_form, home_pitcher, away_pitcher, home_bullpen_penalty, away_bullpen_penalty)

    weather_note, weather_penalty, total_adjust = fetch_weather(home_team, start_time)
    model_home_9, model_away_9 = apply_total_adjustment(model_home_9, model_away_9, total_adjust)
    model_home_5, model_away_5 = apply_total_adjustment(model_home_5, model_away_5, total_adjust * 5 / 9)

    lambda_home_9 = round(0.55 * market_home_9 + 0.45 * model_home_9, 3)
    lambda_away_9 = round(0.55 * market_away_9 + 0.45 * model_away_9, 3)
    lambda_home_5 = round(0.45 * market_home_5 + 0.55 * model_home_5, 3)
    lambda_away_5 = round(0.45 * market_away_5 + 0.55 * model_away_5, 3)

    lineup_status, lineup_penalty = lineup_status_and_penalty(start_time, now_utc)
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
        "away_starter_limited_sample": away_pitcher["limited"],
        "home_starter_limited_sample": home_pitcher["limited"],
        "away_form_note": away_form["note"],
        "home_form_note": home_form["note"],
        "away_pitcher_note": away_pitcher["note"],
        "home_pitcher_note": home_pitcher["note"],
        "model_blend_note": (
            f"FG market {market_away_9:.2f}-{market_home_9:.2f}, custom {model_away_9:.2f}-{model_home_9:.2f}; "
            f"F5 market {market_away_5:.2f}-{market_home_5:.2f}, custom {model_away_5:.2f}-{model_home_5:.2f}."
        ),
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
    bullpen_team_cache = {}
    bullpen_boxscore_cache = {}
    form_cache = {}
    pitcher_cache = {}

    games = []
    skipped = []
    for fixture in fixtures:
        fixture_id = fixture.get("fixtureId")
        if not fixture_id:
            continue
        try:
            book = fetch_odds(fixture_id)
            game = build_game(
                fixture,
                book,
                catalog,
                schedule_map,
                bullpen_team_cache,
                bullpen_boxscore_cache,
                form_cache,
                pitcher_cache,
                now_utc,
            )
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
