#!/usr/bin/env python3
import argparse
import json
import math
import os
import random
import re
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

# Canonical team names (per MLB statsapi) plus common variants.
TEAM_ALIASES = {
    "a's": "Athletics",
    "as": "Athletics",
    "oakland athletics": "Athletics",
    "oakland a's": "Athletics",
    "oakland as": "Athletics",
    "athletics": "Athletics",
    "st louis cardinals": "St. Louis Cardinals",
    "st. louis cardinals": "St. Louis Cardinals",
    "stl cardinals": "St. Louis Cardinals",
    "cardinals": "St. Louis Cardinals",
    "d-backs": "Arizona Diamondbacks",
    "dbacks": "Arizona Diamondbacks",
    "arizona diamondbacks": "Arizona Diamondbacks",
    "diamondbacks": "Arizona Diamondbacks",
    "blue jays": "Toronto Blue Jays",
    "toronto blue jays": "Toronto Blue Jays",
    "red sox": "Boston Red Sox",
    "boston red sox": "Boston Red Sox",
    "white sox": "Chicago White Sox",
    "chicago white sox": "Chicago White Sox",
    "cubs": "Chicago Cubs",
    "chicago cubs": "Chicago Cubs",
    "mets": "New York Mets",
    "new york mets": "New York Mets",
    "yankees": "New York Yankees",
    "yanks": "New York Yankees",
    "new york yankees": "New York Yankees",
    "dodgers": "Los Angeles Dodgers",
    "la dodgers": "Los Angeles Dodgers",
    "los angeles dodgers": "Los Angeles Dodgers",
    "angels": "Los Angeles Angels",
    "la angels": "Los Angeles Angels",
    "los angeles angels": "Los Angeles Angels",
    "giants": "San Francisco Giants",
    "sf giants": "San Francisco Giants",
    "san francisco giants": "San Francisco Giants",
    "padres": "San Diego Padres",
    "san diego padres": "San Diego Padres",
    "royals": "Kansas City Royals",
    "kansas city royals": "Kansas City Royals",
    "reds": "Cincinnati Reds",
    "cincinnati reds": "Cincinnati Reds",
    "guardians": "Cleveland Guardians",
    "cleveland guardians": "Cleveland Guardians",
    "indians": "Cleveland Guardians",
    "pirates": "Pittsburgh Pirates",
    "pittsburgh pirates": "Pittsburgh Pirates",
    "phillies": "Philadelphia Phillies",
    "philadelphia phillies": "Philadelphia Phillies",
    "braves": "Atlanta Braves",
    "atlanta braves": "Atlanta Braves",
    "marlins": "Miami Marlins",
    "miami marlins": "Miami Marlins",
    "florida marlins": "Miami Marlins",
    "brewers": "Milwaukee Brewers",
    "milwaukee brewers": "Milwaukee Brewers",
    "twins": "Minnesota Twins",
    "minnesota twins": "Minnesota Twins",
    "orioles": "Baltimore Orioles",
    "baltimore orioles": "Baltimore Orioles",
    "astros": "Houston Astros",
    "houston astros": "Houston Astros",
    "rangers": "Texas Rangers",
    "texas rangers": "Texas Rangers",
    "rockies": "Colorado Rockies",
    "colorado rockies": "Colorado Rockies",
    "tigers": "Detroit Tigers",
    "detroit tigers": "Detroit Tigers",
    "mariners": "Seattle Mariners",
    "seattle mariners": "Seattle Mariners",
    "nats": "Washington Nationals",
    "nationals": "Washington Nationals",
    "washington nationals": "Washington Nationals",
    "rays": "Tampa Bay Rays",
    "tampa bay rays": "Tampa Bay Rays",
    "devil rays": "Tampa Bay Rays",
    "bluejays": "Toronto Blue Jays",
    "whitesox": "Chicago White Sox",
    "redsox": "Boston Red Sox",
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


def _team_key(name: str) -> str:
    """Canonical key: lowercase, strip all non-alphanumerics (handles St./St, spaces, accents)."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def normalize_team(name: str) -> str:
    if not name:
        return name
    raw = str(name).strip()
    key = _team_key(raw)
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    return raw


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


def odds_get(endpoint: str, params: dict, max_retries: int = 6) -> dict | list:
    """Call OddsPapi with automatic exponential-backoff retry on 429."""
    api_key = env_required("ODDSPAPI_API_KEY")
    merged = {"apiKey": api_key, **params}
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    last_exc = None
    for attempt in range(max_retries + 1):
        response = requests.get(url, params=merged, timeout=TIMEOUT)
        if response.status_code == 429:
            # Honor Retry-After if provided; otherwise exponential backoff
            retry_after = response.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else None
            except Exception:
                wait = None
            if wait is None:
                wait = min(1.5 * (2 ** attempt) + random.uniform(0.0, 0.5), 30.0)
            if attempt < max_retries:
                print(f"[odds] 429 on {endpoint} (attempt {attempt+1}/{max_retries+1}), waiting {wait:.1f}s...")
                import time as _t
                _t.sleep(wait)
                last_exc = ApiError(f"429 Too Many Requests (gave up after {max_retries+1} tries)")
                continue
            raise ApiError(f"429 Too Many Requests (gave up after {max_retries+1} tries)") from None
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ApiError(f"{response.status_code} {response.reason}") from exc
        return response.json()
    # Shouldn't reach here
    raise last_exc if last_exc else ApiError("odds_get failed")


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
    # MLB slate spans US Eastern time. A "July 20" MLB day includes games starting
    # from ~17:00 UTC (1 PM ET early games in Europe/intl) through ~05:00 UTC NEXT day
    # (10 PM PT / 1 AM ET late West Coast games). To be safe we fetch a 48-hour window
    # (from noon UTC of the target day through noon UTC two days later) then filter
    # by US Eastern date so post-midnight UTC games still land on the correct slate.
    start = day.strftime("%Y-%m-%d")
    # Extend "to" to day+2 to capture post-midnight UTC games belonging to this slate.
    end_date = day + timedelta(days=2)
    end = end_date.strftime("%Y-%m-%d")
    data = odds_get("fixtures", {"sportId": SPORT_ID, "from": start, "to": end})
    fixtures = []
    # Target "MLB day" in US Eastern time (UTC-4 in summer) — games 12:00 ET (16:00 UTC)
    # through 04:00 ET next day (08:00 UTC next day).
    # Use UTC windows: from 15:00 UTC on target day to 10:00 UTC 2 days later to be safe.
    window_start = day.replace(hour=15, minute=0, second=0, microsecond=0)
    window_end = (day + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    for row in data:
        if not row.get("hasOdds"):
            continue
        if str(row.get("tournamentName", "")).upper() != "MLB":
            continue
        if str(row.get("statusName", "")).lower() not in {"pre-game", "not started", "scheduled"}:
            continue
        # Filter to games in the correct MLB day window (post-midnight UTC games included)
        st = _parse_iso(row.get("startTime"))
        if st is None or not (window_start <= st < window_end):
            continue
        fixtures.append(row)
    fixtures.sort(key=lambda x: x.get("startTime", ""))
    return fixtures


def fetch_book_odds(fixture_id: str) -> dict:
    data = odds_get("odds", {"fixtureId": fixture_id, "bookmakers": BOOKMAKER})
    return data.get("bookmakerOdds", {}).get(BOOKMAKER, {})


def fetch_schedule_games(day: datetime) -> list[dict]:
    games = []
    # MLB statsapi returns games by the date listed on the game (US date). Late-night games
    # (10 PM PT / 6 AM ET? no — 10 PM PT = 1 AM ET next day = 05:00 UTC next day) can fall on
    # the next UTC date even though they belong to the same MLB slate. Fetch today + tomorrow
    # to cover the whole card, then filter by our window.
    for offset_days in (0, 1):
        d = day + timedelta(days=offset_days)
        payload = safe_get(
            MLB_SCHEDULE_URL,
            {
                "sportId": 1,
                "date": d.strftime("%Y-%m-%d"),
                "gameTypes": "R",
                "hydrate": "probablePitcher,venue",
            },
        )
        for date_row in payload.get("dates", []):
            for game in date_row.get("games", []):
                away_team = normalize_team(game.get("teams", {}).get("away", {}).get("team", {}).get("name", ""))
                home_team = normalize_team(game.get("teams", {}).get("home", {}).get("team", {}).get("name", ""))
                if not away_team or not home_team:
                    continue
                start_iso = game.get("gameDate")
                games.append(
                    {
                        "game_pk": game.get("gamePk"),
                        "start_time": start_iso,
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
    # De-duplicate by game_pk and filter to our window (in case of double-headers/dh)
    window_start = day.replace(hour=15, minute=0, second=0, microsecond=0)
    window_end = (day + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    seen = set()
    deduped = []
    for g in games:
        if g["game_pk"] and g["game_pk"] in seen:
            continue
        if g["game_pk"]:
            seen.add(g["game_pk"])
        st = _parse_iso(g.get("start_time"))
        if st is not None and not (window_start <= st < window_end):
            continue
        deduped.append(g)
    return deduped


def _parse_iso(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def match_schedule_game(away_team: str, home_team: str, start_time: str | None, schedule_games: list[dict]) -> dict:
    away_n = _team_key(away_team)
    home_n = _team_key(home_team)
    target = _parse_iso(start_time)

    def _swap(sched: dict) -> dict:
        """Schedule game was found in reverse direction (sched has our 'away' arg as home and vice-versa).
        Return a copy with away/home flipped so the result matches the caller's requested perspective.
        The caller's away/home argument names are the desired labels; sched's columns are reversed."""
        out = dict(sched)
        out["away_team"] = away_team
        out["home_team"] = home_team
        out["away_team_id"] = TEAM_IDS.get(away_team, sched.get("home_team_id"))
        out["home_team_id"] = TEAM_IDS.get(home_team, sched.get("away_team_id"))
        # sched lists our desired "away" as its home, and our desired "home" as its away
        out["venue"] = sched.get("venue", "N/A")
        out["away_starter"] = sched.get("home_starter", "TBD")
        out["home_starter"] = sched.get("away_starter", "TBD")
        out["away_starter_id"] = sched.get("home_starter_id")
        out["home_starter_id"] = sched.get("away_starter_id")
        return out

    # 1) Exact direction: sched lists (away,home) exactly as OddsPapi
    exact = [g for g in schedule_games if _team_key(g.get("away_team")) == away_n and _team_key(g.get("home_team")) == home_n]
    if exact:
        return exact[0]

    # 2) Reverse direction: sched has (home,away) — Oddspapi swapped vs MLB statsapi.
    swapped = [g for g in schedule_games if _team_key(g.get("away_team")) == home_n and _team_key(g.get("home_team")) == away_n]
    if swapped:
        # Pick the one with closest start time if multiple (doubleheaders)
        if target:
            def _dt_secs(g):
                gt = _parse_iso(g.get("start_time"))
                return abs((gt - target).total_seconds()) if gt else 1e18
            swapped.sort(key=_dt_secs)
        return _swap(swapped[0])

    # 3) Fuzzy: find the schedule game closest in start time that involves either team.
    if target:
        candidates = []
        for g in schedule_games:
            g_away = _team_key(g.get("away_team"))
            g_home = _team_key(g.get("home_team"))
            if g_away == away_n or g_home == away_n or g_away == home_n or g_home == home_n:
                gt = _parse_iso(g.get("start_time"))
                if gt is None:
                    continue
                candidates.append((abs((gt - target).total_seconds()), g_away == away_n and g_home == home_n, g_away == home_n and g_home == away_n, g))
        if candidates:
            candidates.sort(key=lambda x: (x[0], not x[1], not x[2]))  # closest time, prefer exact then reverse
            secs, is_exact, is_rev, best = candidates[0]
            if secs <= 6 * 3600:  # within 6 hours
                if is_rev and not is_exact:
                    return _swap(best)
                return best

    # 4) Fallback: TBD (no match found)
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
            # OddsPapi convention: lower outcomeId = HOME (p1), higher outcomeId = AWAY (p2)
            return {"home": prices[0][1], "away": prices[1][1]}
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
    p1_name = normalize_team(fixture.get("participant1Name") or "Team 1")  # OddsPapi p1 = HOME
    p2_name = normalize_team(fixture.get("participant2Name") or "Team 2")  # OddsPapi p2 = AWAY
    # match_schedule_game expects args in (away, home) order per MLB statsapi convention.
    # OddsPapi lists p1=HOME, p2=AWAY, so pass p2 (away) first, p1 (home) second.
    sched = match_schedule_game(p2_name, p1_name, fixture.get("startTime"), schedule_games)
    away_team = sched.get("away_team") or p2_name
    home_team = sched.get("home_team") or p1_name

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

    # lambdas_from_total_and_ml returns (home_lambda, away_lambda) where "home" and "away"
    # are in the frame of the win prob passed in. Since we pass (home_odds, away_odds)
    # in REAL baseball order (home = real home team), the returned lambdas are also real:
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
    print(f"[slate] Building slate for {now_utc.strftime('%Y-%m-%d')} UTC ({now_utc.isoformat()})")
    catalog = load_market_catalog()
    fixtures = fetch_fixtures(now_utc)
    print(f"[slate] Fetched {len(fixtures)} pre-game fixtures from OddsPapi")
    schedule_games = fetch_schedule_games(now_utc)
    print(f"[slate] Fetched {len(schedule_games)} schedule games from MLB statsapi")
    # Debug: print schedule game team pairs so we can spot mismatches in CI logs
    for g in schedule_games:
        print(f"[slate]   schedule: {g['away_team']} @ {g['home_team']} | starter: {g.get('away_starter','?')} vs {g.get('home_starter','?')}")
    form_cache = {}
    pitcher_cache = {}

    games = []
    skipped = []
    import time as _time
    for i, fixture in enumerate(fixtures):
        fixture_id = fixture.get("fixtureId")
        if not fixture_id:
            continue
        p1_raw = fixture.get("participant1Name") or "Team 1"
        p2_raw = fixture.get("participant2Name") or "Team 2"
        # Stagger between fixture odds requests to reduce 429 chance (odds_get handles its own retries)
        if i > 0:
            _time.sleep(0.8)
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
                        "away": p2_raw,
                        "home": p1_raw,
                    }
                )
        except Exception as exc:
            err_msg = sanitize_error(exc)
            skipped.append(
                {
                    "fixtureId": fixture_id,
                    "reason": err_msg,
                    "away": p2_raw,
                    "home": p1_raw,
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
        print(f"Skipped {len(skipped)} fixtures:")
        for s in skipped:
            print(f"  - {s.get('away')} @ {s.get('home')}: {s.get('reason')}")
    # Report how many games got confirmed starters
    confirmed = sum(1 for g in games if g.get("starters_confirmed"))
    print(f"[slate] Starters confirmed for {confirmed}/{len(games)} games")


if __name__ == "__main__":
    main()
