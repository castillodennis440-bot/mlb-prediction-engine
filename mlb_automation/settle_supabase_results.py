  import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests


TEAM_ALIASES = {
    # AL East
    "baltimoreorioles": "baltimoreorioles",
    "orioles": "baltimoreorioles",

    "bostonredsox": "bostonredsox",
    "redsox": "bostonredsox",
    "red sox": "bostonredsox",

    "newyorkyankees": "newyorkyankees",
    "yankees": "newyorkyankees",

    "tampabayrays": "tampabayrays",
    "rays": "tampabayrays",

    "torontobluejays": "torontobluejays",
    "bluejays": "torontobluejays",
    "blue jays": "torontobluejays",

    # AL Central
    "chicagowhitesox": "chicagowhitesox",
    "whitesox": "chicagowhitesox",
    "white sox": "chicagowhitesox",

    "clevelandguardians": "clevelandguardians",
    "guardians": "clevelandguardians",

    "detroittigers": "detroittigers",
    "tigers": "detroittigers",

    "kansascityroyals": "kansascityroyals",
    "royals": "kansascityroyals",

    "minnesotatwins": "minnesotatwins",
    "twins": "minnesotatwins",

    # AL West
    "houstonastros": "houstonastros",
    "astros": "houstonastros",

    "losangelesangels": "losangelesangels",
    "angels": "losangelesangels",

    "athletics": "athletics",
    "oaklandathletics": "athletics",
    "oaklandas": "athletics",
    "as": "athletics",
    "a's": "athletics",

    "seattlemariners": "seattlemariners",
    "mariners": "seattlemariners",

    "texasrangers": "texasrangers",
    "rangers": "texasrangers",

    # NL East
    "atlantabraves": "atlantabraves",
    "braves": "atlantabraves",

    "miamimarlins": "miamimarlins",
    "marlins": "miamimarlins",

    "newyorkmets": "newyorkmets",
    "mets": "newyorkmets",

    "philadelphiaphillies": "philadelphiaphillies",
    "phillies": "philadelphiaphillies",

    "washingtonnationals": "washingtonnationals",
    "nationals": "washingtonnationals",

    # NL Central
    "chicagocubs": "chicagocubs",
    "cubs": "chicagocubs",

    "cincinnatireds": "cincinnatireds",
    "reds": "cincinnatireds",

    "milwaukeebrewers": "milwaukeebrewers",
    "brewers": "milwaukeebrewers",

    "pittsburghpirates": "pittsburghpirates",
    "pirates": "pittsburghpirates",

    "stlouiscardinals": "stlouiscardinals",
    "cardinals": "stlouiscardinals",
    "st. louis cardinals": "stlouiscardinals",

    # NL West
    "arizonadiamondbacks": "arizonadiamondbacks",
    "diamondbacks": "arizonadiamondbacks",
    "diamond backs": "arizonadiamondbacks",
    "dbacks": "arizonadiamondbacks",
    "d-backs": "arizonadiamondbacks",

    "coloradorockies": "coloradorockies",
    "rockies": "coloradorockies",

    "losangelesdodgers": "losangelesdodgers",
    "dodgers": "losangelesdodgers",

    "sandiegopadres": "sandiegopadres",
    "padres": "sandiegopadres",

    "sanfranciscogiants": "sanfranciscogiants",
    "giants": "sanfranciscogiants",
}


def log(message):
    print(f"[supabase-settle] {message}")


def clean_team_text(name):
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def normalize_team(name):
    """
    Converts team names and nicknames into stable canonical values.

    Examples:
    - "Houston Astros" -> "houstonastros"
    - "Astros" -> "houstonastros"
    - "D-backs" -> "arizonadiamondbacks"
    """
    if not name:
        return ""

    raw = str(name).lower().strip()
    cleaned = clean_team_text(raw)

    # Check cleaned aliases first.
    for alias, canonical in TEAM_ALIASES.items():
        alias_clean = clean_team_text(alias)

        if cleaned == alias_clean:
            return canonical

    # Then check partial aliases.
    for alias, canonical in TEAM_ALIASES.items():
        alias_clean = clean_team_text(alias)

        if alias_clean and alias_clean in cleaned:
            return canonical

    return cleaned


def to_float(value, default=None):
    if value in [None, "", "null", "None"]:
        return default

    try:
        return float(value)
    except Exception:
        return default


def profit_loss_units(status, stake, odds_decimal):
    stake = to_float(stake, 1.0)
    odds_decimal = to_float(odds_decimal)

    if status == "win":
        return round(stake * (odds_decimal - 1), 4)

    if status == "loss":
        return round(-stake, 4)

    if status in ["push", "void"]:
        return 0.0

    return 0.0


def roi_impact(status, stake, odds_decimal):
    stake = to_float(stake, 1.0)

    if not stake:
        return 0.0

    pl = profit_loss_units(status, stake, odds_decimal)

    return round((pl / stake) * 100, 2)


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

    def get_pending_predictions(self, game_date):
        query = (
            f"game_date=eq.{quote(game_date)}"
            f"&status=eq.pending"
            f"&archived=eq.false"
            f"&select=*"
        )

        response = requests.get(
            f"{self.rest}/predictions?{query}",
            headers=self.headers,
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch predictions: {response.status_code} {response.text}"
            )

        return response.json()

    def update_prediction_status(self, prediction_id, status):
        response = requests.patch(
            f"{self.rest}/predictions?id=eq.{quote(str(prediction_id))}",
            headers=self.headers,
            json={"status": status},
            timeout=30,
        )

        if response.status_code not in [200, 204]:
            raise RuntimeError(
                f"Failed to update prediction {prediction_id}: "
                f"{response.status_code} {response.text}"
            )

    def find_result(self, prediction_id):
        response = requests.get(
            f"{self.rest}/results?"
            f"prediction_id=eq.{quote(str(prediction_id))}"
            f"&select=id"
            f"&limit=1",
            headers=self.headers,
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to check result: {response.status_code} {response.text}"
            )

        rows = response.json()

        return rows[0]["id"] if rows else None

    def insert_or_update_result(self, result):
        existing_id = self.find_result(result["prediction_id"])

        if existing_id:
            response = requests.patch(
                f"{self.rest}/results?id=eq.{quote(str(existing_id))}",
                headers=self.headers,
                json=result,
                timeout=30,
            )

            if response.status_code not in [200, 204]:
                raise RuntimeError(
                    f"Failed to update result: {response.status_code} {response.text}"
                )

            return "updated"

        response = requests.post(
            f"{self.rest}/results",
            headers=self.headers,
            json=result,
            timeout=30,
        )

        if response.status_code not in [200, 201]:
            raise RuntimeError(
                f"Failed to insert result: {response.status_code} {response.text}"
            )

        return "inserted"


def fetch_mlb_final_games(game_date):
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={quote(game_date)}&hydrate=linescore"
    )

    log(f"Fetching MLB schedule: {url}")

    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(f"MLB API failed: {response.status_code} {response.text}")

    payload = response.json()
    games = []

    for date_block in payload.get("dates", []):
        for game in date_block.get("games", []):
            status = game.get("status", {})
            abstract_state = status.get("abstractGameState")
            detailed_state = status.get("detailedState", "")

            if abstract_state != "Final" and "Final" not in detailed_state:
                continue

            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})

            away_team = away.get("team", {}).get("name")
            home_team = home.get("team", {}).get("name")

            away_score = away.get("score")
            home_score = home.get("score")

            if away_score is None or home_score is None:
                continue

            innings = game.get("linescore", {}).get("innings", [])

            f5_away = 0
            f5_home = 0

            for inning in innings[:5]:
                f5_away += int(inning.get("away", {}).get("runs") or 0)
                f5_home += int(inning.get("home", {}).get("runs") or 0)

            games.append(
                {
                    "away_team": away_team,
                    "home_team": home_team,
                    "away_norm": normalize_team(away_team),
                    "home_norm": normalize_team(home_team),
                    "away_score": int(away_score),
                    "home_score": int(home_score),
                    "f5_away_score": f5_away,
                    "f5_home_score": f5_home,
                    "game_pk": game.get("gamePk"),
                }
            )

    log(f"Found {len(games)} final MLB games")

    for game in games:
        log(
            f"Final game: {game['away_team']} at {game['home_team']} "
            f"({game['away_norm']} at {game['home_norm']}) "
            f"score {game['away_score']}-{game['home_score']}"
        )

    return games


def match_game(prediction, final_games):
    pred_away = normalize_team(prediction.get("away_team"))
    pred_home = normalize_team(prediction.get("home_team"))

    for game in final_games:
        game_away = game.get("away_norm") or normalize_team(game.get("away_team"))
        game_home = game.get("home_norm") or normalize_team(game.get("home_team"))

        # Exact normalized match.
        if pred_away == game_away and pred_home == game_home:
            return game

        # Partial fallback match.
        away_match = (
            bool(pred_away)
            and bool(game_away)
            and (pred_away in game_away or game_away in pred_away)
        )

        home_match = (
            bool(pred_home)
            and bool(game_home)
            and (pred_home in game_home or game_home in pred_home)
        )

        if away_match and home_match:
            return game

    return None


def selection_side(selection, away_team, home_team):
    selection_norm = normalize_team(selection)
    away_norm = normalize_team(away_team)
    home_norm = normalize_team(home_team)

    if away_norm and away_norm in selection_norm:
        return "away"

    if home_norm and home_norm in selection_norm:
        return "home"

    return None


def settle_prediction(prediction, final_game):
    market = prediction.get("market_type")
    selection = prediction.get("selection") or ""
    line_value = to_float(prediction.get("line_value"))

    away_team = prediction.get("away_team")
    home_team = prediction.get("home_team")

    away_score = int(final_game["away_score"])
    home_score = int(final_game["home_score"])

    f5_away_score = int(final_game.get("f5_away_score", 0))
    f5_home_score = int(final_game.get("f5_home_score", 0))

    side = selection_side(selection, away_team, home_team)

    if market == "Moneyline":
        if side == "away":
            return "win" if away_score > home_score else "loss"

        if side == "home":
            return "win" if home_score > away_score else "loss"

    if market == "Total":
        if line_value is None:
            return None

        total_runs = away_score + home_score
        selection_lower = selection.lower()

        if total_runs == line_value:
            return "push"

        if "over" in selection_lower:
            return "win" if total_runs > line_value else "loss"

        if "under" in selection_lower:
            return "win" if total_runs < line_value else "loss"

    if market == "Run Line":
        if line_value is None or side is None:
            return None

        if side == "away":
            adjusted = away_score + line_value

            if adjusted == home_score:
                return "push"

            return "win" if adjusted > home_score else "loss"

        if side == "home":
            adjusted = home_score + line_value

            if adjusted == away_score:
                return "push"

            return "win" if adjusted > away_score else "loss"

    if market == "F5 Winner":
        if side == "away":
            if f5_away_score == f5_home_score:
                return "push"

            return "win" if f5_away_score > f5_home_score else "loss"

        if side == "home":
            if f5_home_score == f5_away_score:
                return "push"

            return "win" if f5_home_score > f5_away_score else "loss"

    if market == "F5 Handicap":
        if line_value is None or side is None:
            return None

        if side == "away":
            adjusted = f5_away_score + line_value

            if adjusted == f5_home_score:
                return "push"

            return "win" if adjusted > f5_home_score else "loss"

        if side == "home":
            adjusted = f5_home_score + line_value

            if adjusted == f5_away_score:
                return "push"

            return "win" if adjusted > f5_away_score else "loss"

    return None


def default_settlement_date():
    return (date.today() - timedelta(days=1)).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=default_settlement_date())
    args = parser.parse_args()

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise RuntimeError("Missing SUPABASE_URL")

    if not service_role_key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY")

    db = SupabaseRest(supabase_url, service_role_key)

    predictions = db.get_pending_predictions(args.date)
    final_games = fetch_mlb_final_games(args.date)

    log(f"Pending predictions for {args.date}: {len(predictions)}")

    settled = 0
    inserted_results = 0
    updated_results = 0
    skipped_no_final = 0
    skipped_unsettleable = 0

    for prediction in predictions:
        final_game = match_game(prediction, final_games)

        if not final_game:
            skipped_no_final += 1

            log(
                f"No final game match for "
                f"{prediction.get('away_team')} at {prediction.get('home_team')} "
                f"(normalized: "
                f"{normalize_team(prediction.get('away_team'))} at "
                f"{normalize_team(prediction.get('home_team'))})"
            )

            continue

        result_status = settle_prediction(prediction, final_game)

        if result_status not in ["win", "loss", "push", "void"]:
            skipped_unsettleable += 1

            log(
                f"Could not settle prediction: "
                f"{prediction.get('selection')} "
                f"market={prediction.get('market_type')} "
                f"line={prediction.get('line_value')}"
            )

            continue

        pl = profit_loss_units(
            result_status,
            prediction.get("stake_units"),
            prediction.get("odds_decimal"),
        )

        roi = roi_impact(
            result_status,
            prediction.get("stake_units"),
            prediction.get("odds_decimal"),
        )

        result_row = {
            "prediction_id": prediction["id"],
            "final_away_runs": final_game["away_score"],
            "final_home_runs": final_game["home_score"],
            "result_status": result_status,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "profit_loss_units": pl,
            "roi_impact": roi,
        }

        action = db.insert_or_update_result(result_row)
        db.update_prediction_status(prediction["id"], result_status)

        settled += 1

        if action == "inserted":
            inserted_results += 1
        else:
            updated_results += 1

        log(
            f"Settled {prediction.get('selection')} as {result_status} "
            f"({final_game['away_team']} {final_game['away_score']} - "
            f"{final_game['home_team']} {final_game['home_score']})"
        )

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "settlement_date": args.date,
        "pending_predictions": len(predictions),
        "final_games": len(final_games),
        "settled": settled,
        "inserted_results": inserted_results,
        "updated_results": updated_results,
        "skipped_no_final": skipped_no_final,
        "skipped_unsettleable": skipped_unsettleable,
    }

    os.makedirs("mlb_automation", exist_ok=True)

    with open("mlb_automation/settlement_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    log(f"Summary: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
