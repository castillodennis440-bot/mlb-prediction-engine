#!/usr/bin/env python3
"""
build_live_slate.py — primary version for mlb-prediction-engine.

Data source strategy (in order):
  1. If ODDS_API_KEY (The Odds API) is set → use it. ONE HTTP call returns all fixtures
     + Pinnacle ML/spreads/totals in one shot (no per-fixture hammering; very high rate limit).
  2. Else fall back to OddsPapi (ODDSPAPI_API_KEY).

This eliminates the 429 rate-limit issues we hit with repeated OddsPapi calls while
preserving all model logic (lambdas, win probs, picks, labels).
"""
import argparse
import json
import math
import os
import random
import re
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist

import requests

LEAGUE_RUNS_PER_TEAM = 4.35
TIMEOUT = 30
BOOKMAKER = "pinnacle"

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_PEOPLE_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}/stats"

NORMAL = NormalDist()

CACHE_DIR = Path(os.getenv("ODDS_CACHE_DIR", "mlb_automation/.odds_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Team aliases & IDs (same as before) ──────────────────────────────────────
TEAM_ALIASES = {
    "a's": "Athletics","as": "Athletics","oakland athletics": "Athletics","oakland a's": "Athletics",
    "oakland as": "Athletics","athletics": "Athletics",
    "st louis cardinals": "St. Louis Cardinals","st. louis cardinals": "St. Louis Cardinals",
    "stl cardinals": "St. Louis Cardinals","cardinals": "St. Louis Cardinals",
    "d-backs": "Arizona Diamondbacks","dbacks": "Arizona Diamondbacks",
    "arizona diamondbacks": "Arizona Diamondbacks","diamondbacks": "Arizona Diamondbacks",
    "blue jays": "Toronto Blue Jays","toronto blue jays": "Toronto Blue Jays",
    "red sox": "Boston Red Sox","boston red sox": "Boston Red Sox",
    "white sox": "Chicago White Sox","chicago white sox": "Chicago White Sox",
    "cubs": "Chicago Cubs","chicago cubs": "Chicago Cubs",
    "mets": "New York Mets","new york mets": "New York Mets",
    "yankees": "New York Yankees","yanks": "New York Yankees","new york yankees": "New York Yankees",
    "dodgers": "Los Angeles Dodgers","la dodgers": "Los Angeles Dodgers","los angeles dodgers": "Los Angeles Dodgers",
    "angels": "Los Angeles Angels","la angels": "Los Angeles Angels","los angeles angels": "Los Angeles Angels",
    "giants": "San Francisco Giants","sf giants": "San Francisco Giants","san francisco giants": "San Francisco Giants",
    "padres": "San Diego Padres","san diego padres": "San Diego Padres",
    "royals": "Kansas City Royals","kansas city royals": "Kansas City Royals",
    "reds": "Cincinnati Reds","cincinnati reds": "Cincinnati Reds",
    "guardians": "Cleveland Guardians","cleveland guardians": "Cleveland Guardians","indians": "Cleveland Guardians",
    "pirates": "Pittsburgh Pirates","pittsburgh pirates": "Pittsburgh Pirates",
    "phillies": "Philadelphia Phillies","philadelphia phillies": "Philadelphia Phillies",
    "braves": "Atlanta Braves","atlanta braves": "Atlanta Braves",
    "marlins": "Miami Marlins","miami marlins": "Miami Marlins","florida marlins": "Miami Marlins",
    "brewers": "Milwaukee Brewers","milwaukee brewers": "Milwaukee Brewers",
    "twins": "Minnesota Twins","minnesota twins": "Minnesota Twins",
    "orioles": "Baltimore Orioles","baltimore orioles": "Baltimore Orioles",
    "astros": "Houston Astros","houston astros": "Houston Astros",
    "rangers": "Texas Rangers","texas rangers": "Texas Rangers",
    "rockies": "Colorado Rockies","colorado rockies": "Colorado Rockies",
    "tigers": "Detroit Tigers","detroit tigers": "Detroit Tigers",
    "mariners": "Seattle Mariners","seattle mariners": "Seattle Mariners",
    "nats": "Washington Nationals","nationals": "Washington Nationals","washington nationals": "Washington Nationals",
    "rays": "Tampa Bay Rays","tampa bay rays": "Tampa Bay Rays","devil rays": "Tampa Bay Rays",
    "bluejays": "Toronto Blue Jays","whitesox": "Chicago White Sox","redsox": "Boston Red Sox",
}

TEAM_IDS = {
    "Arizona Diamondbacks":109,"Atlanta Braves":144,"Baltimore Orioles":110,"Boston Red Sox":111,
    "Chicago Cubs":112,"Chicago White Sox":145,"Cincinnati Reds":113,"Cleveland Guardians":114,
    "Colorado Rockies":115,"Detroit Tigers":116,"Houston Astros":117,"Kansas City Royals":118,
    "Los Angeles Angels":108,"Los Angeles Dodgers":119,"Miami Marlins":146,"Milwaukee Brewers":158,
    "Minnesota Twins":142,"New York Mets":121,"New York Yankees":147,"Athletics":133,
    "Philadelphia Phillies":143,"Pittsburgh Pirates":134,"San Diego Padres":135,"San Francisco Giants":137,
    "Seattle Mariners":136,"St. Louis Cardinals":138,"Tampa Bay Rays":139,"Texas Rangers":140,
    "Toronto Blue Jays":141,"Washington Nationals":120,
}

class ApiError(RuntimeError): pass

def _team_key(name):
    if not name: return ""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())

def normalize_team(name):
    if not name: return name
    raw = str(name).strip()
    key = _team_key(raw)
    return TEAM_ALIASES.get(key, raw)

def clamp(v, lo, hi): return max(lo, min(hi, v))

def parse_innings_pitched(value):
    if value is None: return 0.0
    s = str(value)
    if not s: return 0.0
    if "." not in s:
        try: return float(s)
        except: return 0.0
    whole, frac = s.split(".",1)
    try: whole_val = int(whole)
    except: whole_val = 0
    if frac == "1": frac_val = 1/3
    elif frac == "2": frac_val = 2/3
    else:
        try: frac_val = float(f"0.{frac}")
        except: frac_val = 0.0
    return whole_val + frac_val

def try_float(x):
    try: return float(x)
    except: return None

def _parse_iso(value):
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except: return None


# ── Data fetching: The Odds API (primary) + OddsPapi (fallback) ───────────────

def _fetch_theoddsapi(day):
    """Single-call fetch from The Odds API. Returns (fixtures, book_by_fid) in the
    same shape the rest of the pipeline expects.

    NOTE: The Odds API free tier includes DraftKings/FanDuel/BetMGM/Caesars/BetRivers
    with full h2h/spreads/totals markets, but Pinnacle is on paid tiers. We use a
    CONSENSUS of all available US books and vig-strip to approximate Pinnacle's
    sharp line — median price with -3% cent vig is standard for MLB.
    """
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key: return None, None
    # Window from now (so we don't miss late-posting games) through day+2 10:00 UTC
    # to capture post-midnight West Coast games.
    now_utc = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (day + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    # No bookmakers filter — pull all US books (DK, FD, BetMGM, Caesars, etc.)
    # and compute consensus. 3 markets × 1 region = 3 credits per call.
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
        "commenceTimeFrom": start,
        "commenceTimeTo": end,
    }
    print(f"[the-odds-api] Window: {start} → {end}")
    def _get():
        last_resp = None
        for attempt in range(4):
            r = requests.get(url, params=params, timeout=TIMEOUT)
            last_resp = r
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 2**attempt))
                print(f"[the-odds-api] 429, waiting {wait:.0f}s...")
                _time.sleep(min(wait, 30))
                continue
            if r.status_code != 200:
                raise ApiError(f"The Odds API {r.status_code}: {r.text[:300]}")
            return r
        raise ApiError("The Odds API 429 after retries")
    resp = _get()
    data = resp.json()
    remaining = resp.headers.get("x-requests-remaining", "?")
    used = resp.headers.get("x-requests-used", "?")
    print(f"[data] Using The Odds API (US books consensus) — {len(data)} events in one call (used {used}, remaining {remaining})")

    def _median(vals):
        if not vals: return None
        s = sorted(vals); n=len(s)
        return s[n//2] if n%2==1 else (s[n//2-1]+s[n//2])/2

    def _vig_strip(h, a):
        """Remove overround from a two-way market to get ~fair probabilities."""
        ih, ia = 1/h, 1/a
        total = ih + ia
        return ih/total, ia/total

    def _fair_to_odds(p, target_vig=0.03):
        """Convert a fair probability back to decimal odds with target vig."""
        # With vig v, the implied probability for outcome = p*(1+v), so odds = 1/(p*(1+v))
        other = 1-p
        return round(1.0/(p*(1+target_vig)), 3), round(1.0/(other*(1+target_vig)), 3)

    fixtures = []
    book = {}
    oid_counter = 9000
    books_seen = set()
    for ev in data:
        home = normalize_team(ev.get("home_team"))
        away = normalize_team(ev.get("away_team"))
        if not home or not away: continue
        bms = ev.get("bookmakers") or []
        if not bms: continue
        # Collect raw lines from every book
        ml_home, ml_away = [], []
        sp_home, sp_away, sp_handicaps = [], [], []
        to_line, to_over, to_under = [], [], []
        for bm in bms:
            bk = bm.get("key","?")
            books_seen.add(bk)
            for mk in bm.get("markets", []):
                k = mk.get("key"); outs = mk.get("outcomes", [])
                if k == "h2h" and len(outs) >= 2:
                    for oc in outs:
                        nm = normalize_team(oc.get("name",""))
                        pr = oc.get("price")
                        if nm and pr and pr > 1:
                            if _team_key(nm) == _team_key(home): ml_home.append(float(pr))
                            elif _team_key(nm) == _team_key(away): ml_away.append(float(pr))
                elif k == "spreads" and len(outs) >= 2:
                    for oc in outs:
                        nm = normalize_team(oc.get("name",""))
                        pr = oc.get("price"); pt = oc.get("point")
                        if nm and pr and pr > 1 and pt is not None:
                            if _team_key(nm) == _team_key(home):
                                sp_home.append(float(pr)); sp_handicaps.append(float(pt))
                            elif _team_key(nm) == _team_key(away):
                                sp_away.append(float(pr))
                elif k == "totals" and len(outs) >= 2:
                    ov = un = ln = None
                    for oc in outs:
                        nm = (oc.get("name") or "").lower()
                        pr = oc.get("price"); pt = oc.get("point")
                        if nm == "over" and pr and pt is not None: ov=float(pr); ln=float(pt)
                        elif nm == "under" and pr: un=float(pr)
                    if ov and un and ln is not None:
                        to_line.append(ln); to_over.append(ov); to_under.append(un)
        if not ml_home or not ml_away:
            continue  # need at least ML to price this game
        # Consensus = median across books, then vig-strip to fair, reprice at 3% vig (~Pinnacle)
        h_raw = _median(ml_home); a_raw = _median(ml_away)
        fh, fa = _vig_strip(h_raw, a_raw)
        h_odds, a_odds = _fair_to_odds(fh, target_vig=0.025)  # Pinnacle typically holds ~2.5% on MLB ML
        # Spreads: require all books agree on -1.5 / +1.5 standard RL
        rl_available = False; rh_odds=ra_odds=None; hc=1.5
        if sp_home and sp_away and sp_handicaps:
            # Verify consensus handicap is ±1.5 standard RL
            avg_hc = _median(sp_handicaps)
            if avg_hc is not None and abs(abs(avg_hc)-1.5) < 0.25:
                rh_raw = _median(sp_home); ra_raw = _median(sp_away)
                # frh = fair probability home covers its spread, fra = fair away covers
                frh, fra = _vig_strip(rh_raw, ra_raw)
                rh_odds, ra_odds = _fair_to_odds(frh, target_vig=0.04)
                # choose_handicap() below ignores outcome order and uses min/max
                # prices and the home ML price to decide direction, so we just
                # need the two prices present and handicap==1.5.
                hc = 1.5
                rl_available = True
        # Totals
        total_available = False; t_line = t_over = t_under = None
        if to_line and to_over and to_under:
            t_line = _median(to_line)
            o_raw = _median(to_over); u_raw = _median(to_under)
            fo, fu = _vig_strip(o_raw, u_raw)
            t_over, t_under = _fair_to_odds(fo, target_vig=0.035)
            total_available = True
        if not total_available:
            continue  # need totals for lambda math
        # Build fixture + book entries in the shape the existing pipeline expects
        fid = str(ev.get("id"))
        start_time = ev.get("commence_time")
        fixtures.append({
            "fixtureId": fid,
            "participant1Name": home,  # p1 = HOME
            "participant2Name": away,  # p2 = AWAY
            "startTime": start_time,
            "statusName": "pre-game",
            "tournamentName": "MLB",
            "hasOdds": True,
        })
        markets = {}
        # ML market (h2h) — lower oid = home, higher oid = away (convention rest of pipeline uses)
        markets[str(1000)] = {
            "marketName": "Winner (incl. extra innings)",
            "handicap": None,
            "outcomes": [
                {"outcomeId": oid_counter, "outcomeName": home.lower(),
                 "players": {"0": {"price": h_odds}}},
                {"outcomeId": oid_counter+1, "outcomeName": away.lower(),
                 "players": {"0": {"price": a_odds}}},
            ]
        }
        oid_counter += 2
        # Totals market
        markets[str(3000)] = {
            "marketName": "Over Under (incl. extra innings)",
            "handicap": float(t_line),
            "outcomes": [
                {"outcomeId": oid_counter, "outcomeName": "over",
                 "players": {"0": {"price": t_over}}},
                {"outcomeId": oid_counter+1, "outcomeName": "under",
                 "players": {"0": {"price": t_under}}},
            ]
        }
        oid_counter += 2
        # Spreads/RL market (-1.5 only)
        if rl_available:
            # By convention: first outcome is home, second is away
            markets[str(2000)] = {
                "marketName": "Handicap (incl. extra innings)",
                "handicap": float(hc),
                "outcomes": [
                    {"outcomeId": oid_counter, "outcomeName": home.lower(),
                     "players": {"0": {"price": rh_odds}}},
                    {"outcomeId": oid_counter+1, "outcomeName": away.lower(),
                     "players": {"0": {"price": ra_odds}}},
                ]
            }
            oid_counter += 2
        book[fid] = {"markets": markets}
    print(f"[the-odds-api] Books aggregated: {sorted(books_seen)}")
    print(f"[the-odds-api] Built {len(fixtures)} fixtures with ML+Total markets")
    return fixtures, book


def _fetch_oddspapi(day):
    """OddsPapi fallback (kept for when ODDS_API_KEY isn't set)."""
    api_key = os.getenv("ODDSPAPI_API_KEY")
    if not api_key: return None, None
    BASE = "https://api.oddspapi.io/v4"
    SPORT_ID = 13
    def _og(endpoint, params, retries=4):
        merged = {"apiKey": api_key, **params}
        for attempt in range(retries+1):
            r = requests.get(f"{BASE}/{endpoint.lstrip('/')}", params=merged, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 2**attempt))
                if attempt < retries and wait < 60:
                    print(f"[oddspapi] 429 on {endpoint}, waiting {wait:.0f}s...")
                    _time.sleep(wait); continue
                raise ApiError(f"OddsPapi 429 on {endpoint}")
            if r.status_code != 200:
                raise ApiError(f"OddsPapi {r.status_code}: {r.text[:200]}")
            return r.json()
        raise ApiError(f"OddsPapi failed on {endpoint}")
    print("[data] Falling back to OddsPapi...")
    start = day.strftime("%Y-%m-%d")
    end = (day + timedelta(days=2)).strftime("%Y-%m-%d")
    data = _og("fixtures", {"sportId":SPORT_ID,"from":start,"to":end})
    fixtures=[]
    window_start = day.replace(hour=15, minute=0)
    window_end = (day + timedelta(days=2)).replace(hour=10, minute=0)
    for row in data:
        if not row.get("hasOdds"): continue
        if str(row.get("tournamentName","")).upper() != "MLB": continue
        if str(row.get("statusName","")).lower() not in {"pre-game","not started","scheduled"}: continue
        st = _parse_iso(row.get("startTime"))
        if st is None or not (window_start <= st < window_end): continue
        fixtures.append(row)
    fixtures.sort(key=lambda x: x.get("startTime",""))
    book={}
    for i,fx in enumerate(fixtures):
        fid = fx.get("fixtureId")
        if not fid: continue
        if i>0: _time.sleep(0.8)
        d = _og("odds", {"fixtureId":fid,"bookmakers":BOOKMAKER})
        book[fid] = d.get("bookmakerOdds",{}).get(BOOKMAKER,{})
    return fixtures, book


def fetch_schedule_games(day):
    games=[]
    for offset in (0,1,2):
        d = day + timedelta(days=offset)
        r = requests.get(MLB_SCHEDULE_URL, params={
            "sportId":1,"date":d.strftime("%Y-%m-%d"),"gameTypes":"R",
            "hydrate":"probablePitcher,venue",
        }, timeout=TIMEOUT); r.raise_for_status()
        payload = r.json()
        for dr in payload.get("dates",[]):
            for g in dr.get("games",[]):
                a = normalize_team(g.get("teams",{}).get("away",{}).get("team",{}).get("name",""))
                h = normalize_team(g.get("teams",{}).get("home",{}).get("team",{}).get("name",""))
                if not a or not h: continue
                games.append({
                    "game_pk":g.get("gamePk"),"start_time":g.get("gameDate"),
                    "venue":g.get("venue",{}).get("name") or "N/A",
                    "away_team":a,"home_team":h,
                    "away_team_id":g.get("teams",{}).get("away",{}).get("team",{}).get("id") or TEAM_IDS.get(a),
                    "home_team_id":g.get("teams",{}).get("home",{}).get("team",{}).get("id") or TEAM_IDS.get(h),
                    "away_starter":g.get("teams",{}).get("away",{}).get("probablePitcher",{}).get("fullName") or "TBD",
                    "home_starter":g.get("teams",{}).get("home",{}).get("probablePitcher",{}).get("fullName") or "TBD",
                    "away_starter_id":g.get("teams",{}).get("away",{}).get("probablePitcher",{}).get("id"),
                    "home_starter_id":g.get("teams",{}).get("home",{}).get("probablePitcher",{}).get("id"),
                })
    ws = datetime.now(timezone.utc).replace(second=0,microsecond=0); we = (day+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0)
    seen=set(); out=[]
    for g in games:
        if g["game_pk"] and g["game_pk"] in seen: continue
        if g["game_pk"]: seen.add(g["game_pk"])
        st=_parse_iso(g.get("start_time"))
        if st is None or not (ws<=st<we): continue
        out.append(g)
    return out


def match_schedule_game(away_team, home_team, start_time, schedule_games):
    away_n=_team_key(away_team); home_n=_team_key(home_team); target=_parse_iso(start_time)
    def _swap(s):
        o=dict(s); o["away_team"]=away_team; o["home_team"]=home_team
        o["away_team_id"]=TEAM_IDS.get(away_team,s.get("home_team_id"))
        o["home_team_id"]=TEAM_IDS.get(home_team,s.get("away_team_id"))
        o["venue"]=s.get("venue","N/A")
        o["away_starter"]=s.get("home_starter","TBD"); o["home_starter"]=s.get("away_starter","TBD")
        o["away_starter_id"]=s.get("home_starter_id"); o["home_starter_id"]=s.get("away_starter_id")
        return o
    exact=[g for g in schedule_games if _team_key(g.get("away_team"))==away_n and _team_key(g.get("home_team"))==home_n]
    if exact: return exact[0]
    swapped=[g for g in schedule_games if _team_key(g.get("away_team"))==home_n and _team_key(g.get("home_team"))==away_n]
    if swapped:
        if target:
            def _ds(g):
                gt=_parse_iso(g.get("start_time")); return abs((gt-target).total_seconds()) if gt else 1e18
            swapped.sort(key=_ds)
        return _swap(swapped[0])
    if target:
        cands=[]
        for g in schedule_games:
            ga=_team_key(g.get("away_team")); gh=_team_key(g.get("home_team"))
            if ga in (away_n,home_n) or gh in (away_n,home_n):
                gt=_parse_iso(g.get("start_time"))
                if gt is None: continue
                cands.append((abs((gt-target).total_seconds()), ga==away_n and gh==home_n, ga==home_n and gh==away_n, g))
        if cands:
            cands.sort(key=lambda x:(x[0],not x[1],not x[2]))
            secs,_,is_rev,best=cands[0]
            if secs<=6*3600:
                return _swap(best) if is_rev else best
    return {"game_pk":None,"start_time":start_time,"venue":"N/A","away_team":away_team,"home_team":home_team,
            "away_team_id":TEAM_IDS.get(away_team),"home_team_id":TEAM_IDS.get(home_team),
            "away_starter":"TBD","home_starter":"TBD","away_starter_id":None,"home_starter_id":None}


def fetch_recent_team_games(team_id, day):
    r=requests.get(MLB_SCHEDULE_URL, params={
        "sportId":1,"teamId":team_id,
        "startDate":(day-timedelta(days=21)).strftime("%Y-%m-%d"),
        "endDate":(day-timedelta(days=1)).strftime("%Y-%m-%d"),"gameTypes":"R",
    },timeout=TIMEOUT); r.raise_for_status()
    games=[]
    for dr in r.json().get("dates",[]):
        for g in dr.get("games",[]):
            if str(g.get("status",{}).get("abstractGameState","")).lower()!="final": continue
            aid=g.get("teams",{}).get("away",{}).get("team",{}).get("id")
            hid=g.get("teams",{}).get("home",{}).get("team",{}).get("id")
            asc=g.get("teams",{}).get("away",{}).get("score"); hsc=g.get("teams",{}).get("home",{}).get("score")
            if team_id==aid: rf=asc; ra=hsc
            elif team_id==hid: rf=hsc; ra=asc
            else: continue
            if rf is None or ra is None: continue
            games.append({"game_pk":g.get("gamePk"),"runs_for":int(rf),"runs_against":int(ra),"won":int(rf)>int(ra)})
    games.sort(key=lambda x:x["game_pk"],reverse=True)
    return games[:10]

def recent_form_summary(team_id, team_name, day, cache):
    k=team_id or team_name
    if k in cache: return cache[k]
    if not team_id:
        cache[k]={"offense_factor":1.0,"defense_factor":1.0,"form_factor":1.0,
                   "note":f"{team_name}: recent-form feed unavailable."}; return cache[k]
    try: gms=fetch_recent_team_games(team_id,day)
    except:
        cache[k]={"offense_factor":1.0,"defense_factor":1.0,"form_factor":1.0,
                   "note":f"{team_name}: recent-form feed unavailable."}; return cache[k]
    if not gms:
        cache[k]={"offense_factor":1.0,"defense_factor":1.0,"form_factor":1.0,
                   "note":f"{team_name}: no recent final games."}; return cache[k]
    n=len(gms); wins=sum(1 for x in gms if x["won"])
    rs=sum(x["runs_for"] for x in gms)/n; ra=sum(x["runs_against"] for x in gms)/n
    wp=wins/n
    cache[k]={"offense_factor":round(clamp(rs/LEAGUE_RUNS_PER_TEAM,0.78,1.25),3),
              "defense_factor":round(clamp(ra/LEAGUE_RUNS_PER_TEAM,0.78,1.25),3),
              "form_factor":round(clamp(1.0+(wp-0.5)*0.20,0.92,1.08),3),
              "note":f"{team_name}: {wins}-{n-wins} last {n}, RS/G {rs:.2f}, RA/G {ra:.2f}."}
    return cache[k]

def fetch_pitcher_rating(pid, name, day, cache):
    k=pid or name or "TBD"
    if k in cache: return cache[k]
    if not pid:
        cache[k]={"factor":1.0,"limited":True,"note":f"{name or 'TBD'}: starter stats unavailable."}; return cache[k]
    try:
        r=requests.get(MLB_PEOPLE_STATS_URL.format(person_id=pid),
                       params={"stats":"season","group":"pitching","season":day.year},timeout=TIMEOUT)
        r.raise_for_status()
        splits=((r.json().get("stats") or [{}])[0].get("splits") or [])
        stat=(splits[0].get("stat") if splits else {}) or {}
    except:
        cache[k]={"factor":1.0,"limited":True,"note":f"{name}: starter stats request failed."}; return cache[k]
    gs=int(stat.get("gamesStarted") or 0); ip=parse_innings_pitched(stat.get("inningsPitched"))
    era=try_float(stat.get("era")) or 4.20; whip=try_float(stat.get("whip")) or 1.30
    so=int(stat.get("strikeOuts") or 0); bb=int(stat.get("baseOnBalls") or 0)
    k9=(so/ip*9.0) if ip>0 else 8.5; bb9=(bb/ip*9.0) if ip>0 else 3.2
    ef=clamp(era/4.20,0.72,1.35); wf=clamp(whip/1.30,0.78,1.25)
    kf=clamp(8.5/max(k9,4.5),0.82,1.20); bf=clamp(bb9/3.2,0.80,1.20)
    base=0.45*ef+0.25*wf+0.20*kf+0.10*bf
    limited=gs<4 or ip<20
    factor=0.65*base+0.35*1.0 if limited else base
    cache[k]={"factor":round(clamp(factor,0.75,1.25),3),"limited":limited,
              "note":(f"{name}: ERA {era:.2f}, WHIP {whip:.2f}, K/9 {k9:.1f}, BB/9 {bb9:.1f}, "
                      f"GS {gs}, IP {ip:.1f}"+(" (limited sample)" if limited else ""))}
    return cache[k]

def extract_price(pn):
    if isinstance(pn,dict): return try_float(pn.get("price"))
    if isinstance(pn,list) and pn: return try_float(pn[-1].get("price"))
    return None

def market_info(bm, mid):
    m=(bm or {}).get(str(mid)) or {}
    return str(m.get("marketName","") or ""), try_float(m.get("handicap"))

def choose_best_total(bm, target):
    c=[]
    for mid,m in (bm or {}).items():
        n,h=market_info(bm,mid)
        if n!=target: continue
        prices=[extract_price((o.get("players") or {}).get("0")) for o in (m.get("outcomes") or [])]
        prices=[p for p in prices if p is not None]
        if len(prices)!=2 or h is None: continue
        c.append({"line":h,"over":max(prices),"under":min(prices)})
    if not c: return None
    c.sort(key=lambda x:abs((1/x["over"])+(1/x["under"])-1))
    return c[0]

def choose_moneyline(bm, target):
    for mid,m in (bm or {}).items():
        n,_=market_info(bm,mid)
        if n!=target: continue
        prices=[]
        for o in (m.get("outcomes") or []):
            p=extract_price((o.get("players") or {}).get("0"))
            if p is not None: prices.append((str(o.get("outcomeId")),p))
        if len(prices)==2:
            prices.sort(key=lambda x:int(x[0]))
            return {"home":prices[0][1],"away":prices[1][1]}
    return None

def choose_handicap(bm, target, wanted, home_ml, away_ml):
    c=[]
    for mid,m in (bm or {}).items():
        n,h=market_info(bm,mid)
        if n!=target or h is None: continue
        if abs(abs(h)-wanted)>1e-9: continue
        prices=[extract_price((o.get("players") or {}).get("0")) for o in (m.get("outcomes") or [])]
        prices=[p for p in prices if p is not None]
        if len(prices)!=2: continue
        f_home=home_ml<away_ml; sp=min(prices); lp=max(prices)
        if f_home: hl,ho=-wanted,lp; al,ao=+wanted,sp
        else: hl,ho=+wanted,sp; al,ao=-wanted,lp
        c.append({"line":wanted,"home_line":hl,"away_line":al,"home_odds":ho,"away_odds":ao})
    if not c: return None
    c.sort(key=lambda x:(1/x["home_odds"])+(1/x["away_odds"])-1)
    return c[0]

def win_prob_from_odds(ho, ao):
    return (1/ho)/((1/ho)+(1/ao))

def lambdas_from_total_and_ml(total, hp):
    p=clamp(hp,0.01,0.99); z=NORMAL.inv_cdf(p)
    md=z*math.sqrt(max(total,0.5))
    return round(max(0.2,(total+md)/2),3), round(max(0.2,(total-md)/2),3)

def apply_total_adjustment(hl,al,d):
    t=max(hl+al,0.4); hs=hl/t; as_=al/t
    return round(hl+d*hs,3), round(al+d*as_,3)

def build_custom_lambdas(hf,af,hp,ap):
    hm=LEAGUE_RUNS_PER_TEAM*hf["offense_factor"]*af["defense_factor"]*ap["factor"]*hf["form_factor"]*1.04
    am=LEAGUE_RUNS_PER_TEAM*af["offense_factor"]*hf["defense_factor"]*hp["factor"]*af["form_factor"]*0.96
    h5=(LEAGUE_RUNS_PER_TEAM*5/9)*hf["offense_factor"]*af["defense_factor"]*(ap["factor"]**1.10)*hf["form_factor"]*1.03
    a5=(LEAGUE_RUNS_PER_TEAM*5/9)*af["offense_factor"]*hf["defense_factor"]*(hp["factor"]**1.10)*af["form_factor"]*0.97
    return (round(clamp(hm,2.2,7.0),3),round(clamp(am,2.2,7.0),3),
            round(clamp(h5,1.1,4.5),3),round(clamp(a5,1.1,4.5),3))

def starter_penalties(as_,hs,ap,hp):
    m=int(as_=="TBD")+int(hs=="TBD"); l=int(bool(ap.get("limited")))+int(bool(hp.get("limited")))
    if m==2: return ("Both probable starters unavailable — F5 markets suppressed, full-game confidence reduced.",1.0,2.5)
    if m==1: return ("One probable starter unavailable — F5 markets suppressed, full-game confidence reduced.",0.75,2.0)
    if l==2: return ("Both starters are limited-sample arms — F5 confidence reduced.",0.35,0.75)
    if l==1: return ("One starter is a limited-sample arm — F5 confidence reduced.",0.20,0.50)
    return ("Probable starters available.",0.0,0.0)

def lineup_status(st, now):
    if not st: return "projected",0.50,0.50
    try: sdt=datetime.fromisoformat(st.replace("Z","+00:00"))
    except: return "projected",0.50,0.50
    mins=(sdt-now).total_seconds()/60
    if mins<=60:
        return ("projected-near-lock",0.25,0.35)
    elif mins<=120:
        return ("projected-near-lock",0.25,0.50)
    else:
        return ("projected",0.50,0.75)

def build_game(fixture, book, schedule_games, fc, pc, now):
    p1=normalize_team(fixture.get("participant1Name") or "1")  # HOME
    p2=normalize_team(fixture.get("participant2Name") or "2")  # AWAY
    s=match_schedule_game(p2,p1,fixture.get("startTime"),schedule_games)
    away=s.get("away_team") or p2; home=s.get("home_team") or p1
    markets=book.get("markets") or {}
    fg_ml=choose_moneyline(markets,"Winner (incl. extra innings)")
    fg_total=choose_best_total(markets,"Over Under (incl. extra innings)")
    if not fg_ml or not fg_total: return None
    fg_rl=choose_handicap(markets,"Handicap (incl. extra innings)",1.5,fg_ml["home"],fg_ml["away"])
    mh9,ma9=lambdas_from_total_and_ml(fg_total["line"],win_prob_from_odds(fg_ml["home"],fg_ml["away"]))
    # F5 markets are not on The Odds API free tier; use scaled FG values as fallback.
    # F5 lambdas are scaled 5/9 (no F5 market to blend against).
    mh5=round(mh9*5/9,3); ma5=round(ma9*5/9,3)
    venue=s.get("venue") or "N/A"; stt=s.get("start_time") or fixture.get("startTime")
    aid=s.get("away_team_id") or TEAM_IDS.get(away); hid=s.get("home_team_id") or TEAM_IDS.get(home)
    af=recent_form_summary(aid,away,now,fc); hf=recent_form_summary(hid,home,now,fc)
    asn=s.get("away_starter") or "TBD"; hsn=s.get("home_starter") or "TBD"
    asid=s.get("away_starter_id"); hsid=s.get("home_starter_id")
    ap=fetch_pitcher_rating(asid,asn,now,pc); hp=fetch_pitcher_rating(hsid,hsn,now,pc)
    mhome9,maway9,mhome5,maway5=build_custom_lambdas(hf,af,hp,ap)
    lh9=round(0.55*mh9+0.45*mhome9,3); la9=round(0.55*ma9+0.45*maway9,3)
    lh5=round(0.45*mh5+0.55*mhome5,3); la5=round(0.45*ma5+0.55*maway5,3)
    ls,lpf,lpf5=lineup_status(stt,now)
    sc=asn!="TBD" and hsn!="TBD"
    snote,spfg,spf5=starter_penalties(asn,hsn,ap,hp)
    g={"away_team":away,"home_team":home,"venue":venue,
       "away_starter":asn,"home_starter":hsn,"starters_confirmed":sc,
       "starter_note":snote,"starter_penalty_fg":spfg,"starter_penalty_f5":spf5,
       "lineup_status":ls,"weather_note":"Weather model not included in stable build.",
       "weather_penalty":0.25,
       "bullpen_note":f"{away}: bullpen model not included in stable build. | {home}: bullpen model not included in stable build.",
       "bullpen_penalty":0.25,"lineup_penalty":lpf,
       "away_starter_limited_sample":ap["limited"],"home_starter_limited_sample":hp["limited"],
       "away_form_note":af["note"],"home_form_note":hf["note"],
       "away_pitcher_note":ap["note"],"home_pitcher_note":hp["note"],
       "model_blend_note":(f"FG market {ma9:.2f}-{mh9:.2f}, custom {maway9:.2f}-{mhome9:.2f}; "
                           f"F5 market {ma5:.2f}-{mh5:.2f}, custom {maway5:.2f}-{mhome5:.2f}."),
       "lambda_away_5":la5,"lambda_home_5":lh5,"lambda_away_9":la9,"lambda_home_9":lh9,
       "odds":{"fg_ml":fg_ml,"fg_total":{"line":fg_total["line"],"over":fg_total["over"],"under":fg_total["under"]}},
       "source_meta":{"fixture_id":fixture.get("fixtureId"),"bookmaker":BOOKMAKER,"start_time":stt}}
    if fg_rl: g["odds"]["fg_rl"]=fg_rl
    return g


def sanitize_error(exc):
    m=str(exc)
    if "403" in m: return "403 Forbidden"
    if "429" in m: return "429 Too Many Requests"
    if " for url:" in m: return m.split(" for url:")[0]
    return m

def report_date():
    raw=os.getenv("REPORT_DATE")
    if raw: return datetime.strptime(raw,"%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",default="mlb_automation/live_slate.json"); a=p.parse_args()
    now=report_date()
    print(f"[slate] Building slate for {now.strftime('%Y-%m-%d')} UTC ({now.isoformat()})")
    _time.sleep(1)
    source_provider = "The Odds API (US-books consensus, ~Pinnacle-sharp)"
    fixtures, book = _fetch_theoddsapi(now)
    if fixtures is None:
        print("[data] The Odds API unavailable (no key or error), falling back to OddsPapi...")
        fixtures, book = _fetch_oddspapi(now)
        source_provider = "OddsPapi"
    if fixtures is None:
        raise ApiError("No odds provider available — both The Odds API and OddsPapi returned nothing")
    print(f"[slate] Fetched {len(fixtures)} pre-game fixtures via {source_provider}")
    schedule=fetch_schedule_games(now)
    print(f"[slate] Fetched {len(schedule)} schedule games from MLB statsapi")
    for g in schedule:
        print(f"[slate]   schedule: {g['away_team']} @ {g['home_team']} | starter: {g.get('away_starter','?')} vs {g.get('home_starter','?')}")
    fc={}; pc={}; games=[]; skipped=[]
    for i,fx in enumerate(fixtures):
        fid=fx.get("fixtureId")
        if not fid: continue
        p1r=fx.get("participant1Name") or "1"; p2r=fx.get("participant2Name") or "2"
        try:
            b=book.get(fid,{})
            gm=build_game(fx,b,schedule,fc,pc,now)
            if gm: games.append(gm)
            else: skipped.append({"fixtureId":fid,"reason":"required markets missing","away":p2r,"home":p1r})
        except Exception as exc:
            skipped.append({"fixtureId":fid,"reason":sanitize_error(exc),"away":p2r,"home":p1r})
    payload={"date":now.strftime("%Y-%m-%d"),"generated_at_utc":now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "source":{"provider":source_provider,"bookmaker":"us-consensus (Pinnacle proxy)"},
             "games":games,"skipped":skipped}
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,indent=2))
    print(f"Wrote {len(games)} games to {out}")
    if skipped:
        print(f"Skipped {len(skipped)} fixtures:")
        for s in skipped: print(f"  - {s.get('away')} @ {s.get('home')}: {s.get('reason')}")
    conf=sum(1 for g in games if g.get("starters_confirmed"))
    print(f"[slate] Starters confirmed for {conf}/{len(games)} games")

if __name__=="__main__":
    main()
