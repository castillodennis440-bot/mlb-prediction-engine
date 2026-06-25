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


def fetch_mlb_schedule_games(day: datetime) -> list[dict]:
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
            games.append({
                "game_pk": game.get("gamePk"),
                "start_time": game.get("gameDate"),
                "venue": game.get("venue", {}).get("name"),
                "away_team": away_team,
                "home_team": home_team,
                "away_team_id": game.get("teams", {}).get("away", {}).get("team", {}).get("id") or TEAM_IDS.get(away_team),
                "home_team_id": game.get("teams", {}).get("home", {}).get("team", {}).get("id") or TEAM_IDS.get(home_team),
                "away_starter": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName"),
                "home_starter": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName"),
                "away_starter_id": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id"),
                "home_starter_id": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id"),
            })
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
                        (
                            datetime.fromisoformat(g["start_time"].replace("Z", "+00:00")) - target
                        ).total_seconds()
                    )
                )
                return candidates[0]
        except Exception:
            pass

    return {
        "game_pk": None,
        "start_time": start_time,
        "venue": TEAM_PARKS.get(home_team, {}).get("venue"),
        "away_team": away_team,
        "home_team": home_team,
        "away_team_id": TEAM_IDS.get(away_team),
        "home_team_id": TEAM_IDS.get(home_team),
        "away_starter": None,
        "home_starter": None,
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
    cache[cache_key] = result
    return result


def bullpen_summary(team_id: int | None, team_name: str, day: datetime, team_cache: dict, boxscore_cache: dict) -> tuple[str, float]:
    cache_key = team_id or team_name
    if cache_key in team_cache:
        return team_cache[cache_key]

    if not team_id:
        note = f"{team_name}: bullpen data unavailable."
        team_cache[cache_key] = (note, 0.25)
        return team_cache[cache_key]

    try:
        recent_games = fetch_recent_team_games(team_id, day)
    except Exception:
        note = "Bullpen usage feed unavailable; neutral bullpen penalty applied."
        team_cache[cache_key] = (note, 0.25)
        return team_cache[cache_key]

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
    team_cache[cache_key] = (note, penalty)
    return team_cache[cache_key]


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

        temp_values = hours.get("temperature_2m", [None])
        precip_values = hours.get("precipitation_probability", [0])
        wind_values = hours.get("wind_speed_10m", [0])

        temp = float(temp_values[idx])
        precip = float(precip_values[idx] or 0)
        wind = float(wind_values[idx] or 0)
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
