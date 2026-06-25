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
SPORT_ID = int(os.getenv("ODDSPAPI_SPORT_ID", "13"))
BOOKMAKER = os.getenv("ODDSPAPI_BOOKMAKER", "pinnacle")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
LEAGUE_RUNS_PER_TEAM = 4.35

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_PEOPLE_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}/stats"

NORMAL = NormalDist()

TEAM_ALIASES = {
    "A's": "Athletics",
    "Oakland Athletics": "Athletics",
    "Athletics": "Athletics",
}

TEAM_IDS = {
    "Arizona Diamondbacks": 109,
    "Atlanta Braves": 144,
    "Baltimore Orioles": 110,
    "Boston Red Sox": 111,
    "Chicago Cubs": 112,
    "Chicago White Sox": 145,
    "Cincinnati Reds": 113,
    "Cleveland Guardians": 114,
    "Colorado Rockies": 115,
    "Detroit Tigers": 116,
    "Houston Astros": 117,
    "Kansas City Royals": 118,
    "Los Angeles Angels": 108,
    "Los Angeles Dodgers": 119,
    "Miami Marlins": 146,
    "Milwaukee Brewers": 158,
    "Minnesota Twins": 142,
    "New York Mets": 121,
    "New York Yankees": 147,
    "Athletics": 133,
    "Philadelphia Phillies": 143,
    "Pittsburgh Pirates": 134,
    "San Diego Padres": 135,
    "San Francisco Giants": 137,
    "Seattle Mariners": 136,
    "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139,
    "Texas Rangers": 140,
    "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
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


def safe_get(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def odds_get(endpoint: str, params: dict) -> dict | list:
    api_key = env_required("ODDSPAPI_API_KEY")
    merged = {"apiKey": api_key, **params}
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    response = requests.get(url, params=merged, timeout=TIMEOUT)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ApiError(f"{response.status_code} {response.reason}") from exc
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
    data = odds_get("markets", {"sportId": SPORT_ID})
    catalog = {}
    for row in data:
        market_id = int(row["marketId"])
        outcomes = {}
        for out in row.get("outcomes", []) or []:
            try:
                outcomes[str(out.get("outcomeId"))] = str(out.get("outcomeName", "")).lower()
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
    data = odds_get("fixtures", {"sportId": SPORT_ID, "from": start, "to": end})
    fixtures = []
    for row in data:
        if not row.get("hasOdds"):
            continue
        if str(row.get("tournamentName", "")).upper() != "MLB":
            continue
        if str(row.get("statusName", "")).lower() not in {"pre-game", "not started", "scheduled"}:
            continue
        fixtures.append(row)
    fixtures.sort(key=lambda x: x.get("startTime", ""))
    return fixtures


def fetch_book_odds(fixture_id: str) -> dict:
    data = odds_get("odds", {"fixtureId": fixture_id, "bookmakers": BOOKMAKER})
    return data.get("bookmakerOdds", {}).get(BOOKMAKER, {})


def fetch_schedule_games(day: datetime) -> list[dict]:
    payload = safe_get(
        MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "date": day.strftime("%Y-%m-%d"),
            "gameTypes": "R",
            "hydrate": "probablePitcher,venue",
        },
    )
    games = []
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            away_team = normalize_team(game.get("teams", {}).get("away", {}).get("team", {}).get("name", ""))
            home_team = normalize_team(game.get("teams", {}).get("home", {}).get("team", {}).get("name", ""))
            if not away_team or not home_team:
                continue
            games.append(
                {
                    "game_pk": game.get("gamePk"),
                    "start_time": game.get("gameDate"),
                    "venue": game.get("venue", {}).get("name") or "N/A",
                    "away_team": away_team,
                    "home_team": home_team,
                    "away_team_id": game.get("teams", {}).get("away", {}).get("team", {}).get("id") or TEAM_IDS.get(away_team),
                    "home_team_id": game.get("teams", {}).get("home", {}).get("team", {}).get("id") or TEAM_IDS.get(home_team),
                    "away_starter": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName") or "TBD",
                    "home_starter": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName") or "TBD",
                    "away_starter_id": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id"),
                    "home_starter_id": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id"),
                }
            )
    return games


def match_schedule_game(away_team: str, home_team: str, start_time: str | None, schedule_games: list[dict]) -> dict:
    exact = [g for g in schedule_games if g["away_team"] == away_team and g["home_team"] == home_team]
    if exact:
        return exact[0]

    if start_time:
        try:
            target = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            candidates = [g for g in schedule_games if g["home_team"] == home_team or g["away_team"] == away_team]
            if candidates:
                candidates.sort(
                    key=lambda g: abs(
                        (datetime.fromisoformat(g["start_time"].replace("Z", "+00:00")) - target).total_seconds()
                    )
                )
                return candidates[0]
        except Exception:
            pass

    return {
        "game_pk": None,
        "start_time": start_time,
        "venue": "N/A",
        "away_team": away_team,
        "home_team": home_team,
        "away_team_id": TEAM_IDS.get(away_team),
        "home_team_id": TEAM_IDS.get(home_team),
        "away_starter": "TBD",
        "home_starter": "TBD",
        "away_starter_id": None,
        "home_starter_id": None,
    }


def fetch_recent_team_games(team_id: int, day: datetime) -> list[dict]:
    payload = safe_get(
        MLB_SCHEDULE_URL,
        {
            "sportId": 1,
            "teamId": team_id,
            "startDate": (day - timedelta(days=21)).strftime("%Y-%m-%d"),
            "endDate": (day - timedelta(days=1)).strftime("%Y-%m-%d"),
            "gameTypes": "R",
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
            elif team_id == home_id:
                runs_for = home_score
                runs_against = away_score
            else:
                continue
            if runs_for is None or runs_against is None:
                continue
            games.append({
                "game_pk": game_pk,
                "runs_for": int(runs_for),
                "runs_against": int(runs_against),
                "won": int(runs_for) > int(runs_against),
            })
    games.sort(key=lambda x: x["game_pk"], reverse=True)
    return games[:10]


def recent_form_summary(team_id: int | None, team_name: str, day: datetime, cache: dict) -> dict:
    cache_key = team_id or team_name
    if cache_key in cache:
        return cache[cache_key]

    if not team_id:
        result = {
            "offense_factor": 1.0,
            "defense_factor": 1.0,
            "form_factor": 1.0,
            "note": f"{team_name}: recent-form feed unavailable.",
        }
        cache[cache_key] = result
        return result

    try:
        games = fetch_recent_team_games(team_id, day)
    except Exception:
        result = {
            "offense_factor": 1.0,
            "defense_factor": 1.0,
            "form_factor": 1.0,
            "note": f"{team_name}: recent-form feed unavailable.",
        }
        cache[cache_key] = result
        return result

    if not games:
        result = {
            "offense_factor": 1.0,
            "defense_factor": 1.0,
            "form_factor": 1.0,
            "note": f"{team_name}: no recent final games found.",
        }
        cache[cache_key] = result
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
    cache[cache_key] = result
    return result


def fetch_pitcher_rating(person_id: int | None, pitcher_name: str, day: datetime, cache: dict) -> dict:
    cache_key = person_id or pitcher_name or "TBD"
    if cache_key in cache:
        return cache[cache_key]

    if not person_id:
        result = {
            "factor": 1.0,
            "limited": True,
            "note": f"{pitcher_name or 'TBD'}: starter stats unavailable.",
        }
        cache[cache_key] = result
        return result

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
        cache[cache_key] = result
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
    factor = 0.65 * base_factor + 0.35 * 1.0 if limited else base_factor

    result = {
        "factor": round(clamp(factor, 0.75, 1.25), 3),
        "limited": limited,
        "note": (
            f"{pitcher_name}: ERA {era:.2f}, WHIP {whip:.2f}, K/9 {k9:.1f}, BB/9 {bb9:.1f}, "
            f"GS {games_started}, IP {innings_pitched:.1f}"
            + (" (limited sample)" if limited else "")
        ),
    }
    cache[cache_key] = result
    return result


def bullpen_summary(team_name: str) -> tuple[str, float]:
    return (f"{team_name}: bullpen model not included in stable build.", 0.25)


def fetch_weather() -> tuple[str, float, float]:
    return ("Weather model not included in stable build.", 0.25, 0.0)


def try_float(x):
    try:
        return float(x)
    except Exception:
        return None


def extract_price(player_node) -> float | None:
    if isinstance(player_node, dict):
        return try_float(player_node.get("price"))
    if isinstance(player_node, list) and player_node:
        return try_float(player_node[-1].get("price"))
    return None


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
        prices = [extract_price((onode.get("players") or {}).get("0")) for onode in outcomes.values()]
        prices = [p for p in prices if p is not None]
        if len(prices) != 2 or handicap is None:
            continue
        over_price = max(prices)
        under_price = min(prices)
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
            price = extract_price((onode.get("players") or {}).get("0"))
            if price is not None:
                prices.append((str(oid), price))
        if len(prices) == 2:
            prices.sort(key=lambda x: int(x[0]))
            return {"away": prices[0][1], "home": prices[1][1]}
    return None


def choose_handicap(book_markets: dict, catalog: dict[int, dict], target_phrase: str, wanted_line: float, home_ml: float, away_ml: float) -> dict | None:
    candidates = []
    for mid, market in (book_markets or {}).items():
        name, handicap = market_info(catalog, mid)
        if name != target_phrase or handicap is None:
            continue
        if abs(abs(handicap) - wanted_line) > 1e-9:
            continue
        outcomes = market.get("outcomes", {}) or {}
        prices = [extract_price((onode.get("players") or {}).get("0")) for onode in outcomes.values()]
        prices = [p for p in prices if p is not None]
        if len(prices) != 2:
            continue

        favorite_is_home = home_ml < away_ml
        short_price = min(prices)
        long_price = max(prices)

        if favorite_is_home:
            home_line, home_odds = -wanted_line, long_price
            away_line, away_odds = +wanted_line, short_price
        else:
            home_line, home_odds = +wanted_line, short_price
            away_line, away_odds = -wanted_line, long_price

        hold = (1 / home_odds) + (1 / away_odds) - 1
        candidates.append(
            {
                "line": wanted_line,
                "home_line": home_line,
                "away_line": away_line,
                "home_odds": home_odds,
                "away_odds": away_odds,
                "hold": hold,
            }
        )
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


def build_custom_lambdas(home_form: dict, away_form: dict, home_pitcher: dict, away_pitcher: dict) -> tuple[float, float, float, float]:
    home_model_9 = LEAGUE_RUNS_PER_TEAM * home_form["offense_factor"] * away_form["defense_factor"] * away_pitcher["factor"] * home_form["form_factor"] * 1.04
    away_model_9 = LEAGUE_RUNS_PER_TEAM * away_form["offense_factor"] * home_form["defense_factor"] * home_pitcher["factor"] * away_form["form_factor"] * 0.96
    home_model_5 = (LEAGUE_RUNS_PER_TEAM * 5.0 / 9.0) * home_form["offense_factor"] * away_form["defense_factor"] * (away_pitcher["factor"] ** 1.10) * home_form["form_factor"] * 1.03
    away_model_5 = (LEAGUE_RUNS_PER_TEAM * 5.0 / 9.0) * away_form["offense_factor"] * home_form["defense_factor"] * (home_pitcher["factor"] ** 1.10) * away_form["form_factor"] * 0.97
    return (
        round(clamp(home_model_9, 2.2, 7.0), 3),
        round(clamp(away_model_9, 2.2, 7.0), 3),
        round(clamp(home_model_5, 1.1, 4.5), 3),
        round(clamp(away_model_5, 1.1, 4.5), 3),
    )


def starter_penalties(away_starter: str, home_starter: str, away_pitcher: dict, home_pitcher: dict) -> tuple[str, float, float]:
    missing = int(away_starter == "TBD") + int(home_starter == "TBD")
    limited = int(bool(away_pitcher.get("limited"))) + int(bool(home_pitcher.get("limited")))
    if missing == 2:
        return ("Both probable starters unavailable — F5 markets suppressed, full-game confidence reduced.", 1.0, 2.5)
    if missing == 1:
        return ("One probable starter unavailable — F5 markets suppressed, full-game confidence reduced.", 0.75, 2.0)
    if limited == 2:
        return ("Both starters are limited-sample arms — F5 confidence reduced.", 0.35, 0.75)
    if limited == 1:
        return ("One starter is a limited-sample arm — F5 confidence reduced.", 0.20, 0.50)
    return ("Probable starters available.", 0.0, 0.0)


def lineup_status_and_penalty(start_time: str | None, now_utc: datetime) -> tuple[str, float]:
    if not start_time:
        return "projected", 0.50
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except Exception:
        return "projected", 0.50
    mins = (start_dt - now_utc).total_seconds() / 60.0
    return ("projected-near-lock", 0.25) if mins <= 120 else ("projected", 0.50)


def build_game(
    fixture: dict,
    book: dict,
    catalog: dict[int, dict],
    schedule_games: list[dict],
    form_cache: dict,
    pitcher_cache: dict,
    now_utc: datetime,
) -> dict | None:
    away_team = normalize_team(fixture.get("participant1Name") or fixture.get("awayTeamName") or "Away Team")
    home_team = normalize_team(fixture.get("participant2Name") or fixture.get("homeTeamName") or "Home Team")
    sched = match_schedule_game(away_team, home_team, fixture.get("startTime"), schedule_games)

    markets = book.get("markets") or {}
    fg_ml = choose_moneyline(markets, catalog, "Winner (incl. extra innings)")
    fg_total = choose_best_total(markets, catalog, "Over Under (incl. extra innings)")
    if not fg_ml or not fg_total:
        return None

    fg_rl = choose_handicap(markets, catalog, "Handicap (incl. extra innings)", 1.5, fg_ml["home"], fg_ml["away"])
    f5_ml = choose_moneyline(markets, catalog, "Winner 1st 5 innings")
    f5_total = choose_best_total(markets, catalog, "Over Under 1st 5 innings")
    f5_reference = f5_ml if f5_ml else fg_ml
    f5_rl = choose_handicap(markets, catalog, "Handicap 1st 5 innings", 0.5, f5_reference["home"], f5_reference["away"]) if f5_reference else None

    market_home_9, market_away_9 = lambdas_from_total_and_ml(fg_total["line"], win_prob_from_odds(fg_ml["home"], fg_ml["away"]))
    if f5_ml and f5_total:
        market_home_5, market_away_5 = lambdas_from_total_and_ml(f5_total["line"], win_prob_from_odds(f5_ml["home"], f5_ml["away"]))
    else:
        market_home_5 = round(market_home_9 * 5 / 9, 3)
        market_away_5 = round(market_away_9 * 5 / 9, 3)

    venue = sched.get("venue") or "N/A"
    start_time = sched.get("start_time") or fixture.get("startTime")
    away_team_id = sched.get("away_team_id") or TEAM_IDS.get(away_team)
    home_team_id = sched.get("home_team_id") or TEAM_IDS.get(home_team)

    away_form = recent_form_summary(away_team_id, away_team, now_utc, form_cache)
    home_form = recent_form_summary(home_team_id, home_team, now_utc, form_cache)

    away_starter = sched.get("away_starter") or "TBD"
    home_starter = sched.get("home_starter") or "TBD"
    away_starter_id = sched.get("away_starter_id")
    home_starter_id = sched.get("home_starter_id")
    away_pitcher = fetch_pitcher_rating(away_starter_id, away_starter, now_utc, pitcher_cache)
    home_pitcher = fetch_pitcher_rating(home_starter_id, home_starter, now_utc, pitcher_cache)

    away_bullpen_note, away_bullpen_penalty = bullpen_summary(away_team)
    home_bullpen_note, home_bullpen_penalty = bullpen_summary(home_team)
    bullpen_penalty = round(max(away_bullpen_penalty, home_bullpen_penalty), 2)
    bullpen_note = f"{away_bullpen_note} | {home_bullpen_note}"

    model_home_9, model_away_9, model_home_5, model_away_5 = build_custom_lambdas(home_form, away_form, home_pitcher, away_pitcher)
    weather_note, weather_penalty, total_adjust = fetch_weather()
    model_home_9, model_away_9 = apply_total_adjustment(model_home_9, model_away_9, total_adjust)
    model_home_5, model_away_5 = apply_total_adjustment(model_home_5, model_away_5, total_adjust * 5 / 9)

    lambda_home_9 = round(0.55 * market_home_9 + 0.45 * model_home_9, 3)
    lambda_away_9 = round(0.55 * market_away_9 + 0.45 * model_away_9, 3)
    lambda_home_5 = round(0.45 * market_home_5 + 0.55 * model_home_5, 3)
    lambda_away_5 = round(0.45 * market_away_5 + 0.55 * model_away_5, 3)

    lineup_status, lineup_penalty = lineup_status_and_penalty(start_time, now_utc)
    starters_confirmed = away_starter != "TBD" and home_starter != "TBD"
    starter_note, starter_penalty_fg, starter_penalty_f5 = starter_penalties(away_starter, home_starter, away_pitcher, home_pitcher)

    game = {
        "away_team": away_team,
        "home_team": home_team,
        "venue": venue,
        "away_starter": away_starter,
        "home_starter": home_starter,
        "starters_confirmed": starters_confirmed,
        "starter_note": starter_note,
        "starter_penalty_fg": starter_penalty_fg,
        "starter_penalty_f5": starter_penalty_f5,
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
    schedule_games = fetch_schedule_games(now_utc)
    form_cache = {}
    pitcher_cache = {}

    games = []
    skipped = []
    for fixture in fixtures:
        fixture_id = fixture.get("fixtureId")
        if not fixture_id:
            continue
        try:
            book = fetch_book_odds(fixture_id)
            game = build_game(fixture, book, catalog, schedule_games, form_cache, pitcher_cache, now_utc)
            if game:
                games.append(game)
            else:
                skipped.append(
                    {
                        "fixtureId": fixture_id,
                        "reason": "required markets missing",
                        "away": fixture.get("participant1Name"),
                        "home": fixture.get("participant2Name"),
                    }
                )
        except Exception as exc:
            skipped.append(
                {
                    "fixtureId": fixture_id,
                    "reason": sanitize_error(exc),
                    "away": fixture.get("participant1Name"),
                    "home": fixture.get("participant2Name"),
                }
            )

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
