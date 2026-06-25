#!/usr/bin/env python3
import argparse
import json
import math
from datetime import datetime
from pathlib import Path

MAX_RUNS = 20
DISPLAY_TIERS = {"LEAN": 1, "BET": 2, "STRONG BET": 3, "BANKER": 4}
TIER_ORDER = ["SKIP", "LEAN", "BET", "STRONG BET", "BANKER"]


def poisson_pmf(k: int, lam: float) -> float:
    if lam < 0:
        raise ValueError("lambda must be non-negative")
    if k < 0:
        return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_series(lam: float, max_runs: int = MAX_RUNS):
    probs = [poisson_pmf(k, lam) for k in range(max_runs)]
    tail = max(0.0, 1.0 - sum(probs))
    probs.append(tail)
    return probs


def joint_probs(home_lam: float, away_lam: float, max_runs: int = MAX_RUNS):
    home = poisson_series(home_lam, max_runs)
    away = poisson_series(away_lam, max_runs)
    return home, away


def probs_from_distributions(home, away):
    p_home = 0.0
    p_away = 0.0
    p_tie = 0.0
    p_home_by_2 = 0.0
    p_away_by_2 = 0.0
    p_home_plus_1_5 = 0.0
    p_away_plus_1_5 = 0.0
    total_probs = {}

    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            p = ph * pa
            total = h + a
            total_probs[total] = total_probs.get(total, 0.0) + p
            diff = h - a

            if diff > 0:
                p_home += p
                if diff >= 2:
                    p_home_by_2 += p
            elif diff < 0:
                p_away += p
                if -diff >= 2:
                    p_away_by_2 += p
            else:
                p_tie += p

            if diff >= -1:
                p_home_plus_1_5 += p
            if diff <= 1:
                p_away_plus_1_5 += p

    return {
        "home_win_9": p_home,
        "away_win_9": p_away,
        "tie_9": p_tie,
        "home_ml": p_home + 0.5 * p_tie,
        "away_ml": p_away + 0.5 * p_tie,
        "home_minus_1_5": p_home_by_2,
        "away_minus_1_5": p_away_by_2,
        "home_plus_1_5": p_home_plus_1_5,
        "away_plus_1_5": p_away_plus_1_5,
        "total_probs": total_probs,
    }


def f5_probs(home, away):
    p_home_lead = 0.0
    p_away_lead = 0.0
    p_tie = 0.0
    total_probs = {}

    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            p = ph * pa
            total = h + a
            total_probs[total] = total_probs.get(total, 0.0) + p
            if h > a:
                p_home_lead += p
            elif a > h:
                p_away_lead += p
            else:
                p_tie += p

    non_tie = max(1e-12, 1.0 - p_tie)
    return {
        "home_lead": p_home_lead,
        "away_lead": p_away_lead,
        "tie": p_tie,
        "home_ml_cond": p_home_lead / non_tie,
        "away_ml_cond": p_away_lead / non_tie,
        "home_minus_0_5": p_home_lead,
        "away_minus_0_5": p_away_lead,
        "home_plus_0_5": p_home_lead + p_tie,
        "away_plus_0_5": p_away_lead + p_tie,
        "total_probs": total_probs,
    }


def over_prob(total_probs, line: float) -> float:
    threshold = math.floor(line) + 1
    return sum(p for total, p in total_probs.items() if total >= threshold)


def under_prob(total_probs, line: float) -> float:
    return 1.0 - over_prob(total_probs, line)


def fair_probability(odds_a: float, odds_b: float) -> float:
    implied_a = 1.0 / odds_a
    implied_b = 1.0 / odds_b
    return implied_a / (implied_a + implied_b)


def hold(odds_a: float, odds_b: float) -> float:
    return (1.0 / odds_a) + (1.0 / odds_b) - 1.0


def classify_tier(adjusted_edge: float, ev: float, hold_pct: float, capped: str | None = None,
                  major_sample: bool = False, major_lineup: bool = False, extreme_weather: bool = False):
    tier = "SKIP"
    if adjusted_edge >= 8.0 and ev >= 0.08 and hold_pct <= 5.0 and not major_sample and not major_lineup and not extreme_weather:
        tier = "BANKER"
    elif adjusted_edge >= 5.0 and ev >= 0.05 and hold_pct <= 6.0:
        tier = "STRONG BET"
    elif adjusted_edge >= 3.0 and ev >= 0.03:
        tier = "BET"
    elif adjusted_edge >= 1.5 and ev > 0:
        tier = "LEAN"

    if capped and TIER_ORDER.index(tier) > TIER_ORDER.index(capped):
        tier = capped
    return tier


def apply_cap(current_cap: str | None, new_cap: str) -> str:
    if current_cap is None:
        return new_cap
    return current_cap if TIER_ORDER.index(current_cap) <= TIER_ORDER.index(new_cap) else new_cap


def penalty_for_market(game, market_key: str, is_alt: bool = False):
    p = 0.0
    notes = []

    limited_away = game.get("away_starter_limited_sample", False)
    limited_home = game.get("home_starter_limited_sample", False)
    both_limited = limited_away and limited_home
    if both_limited:
        p += 2.5
        notes.append("both starters limited sample")
    elif limited_away or limited_home:
        p += 1.5
        notes.append("one starter limited sample")

    starter_penalty = float(game.get("starter_penalty_f5", 0.0) if market_key.startswith("f5") else game.get("starter_penalty_fg", 0.0))
    if starter_penalty:
        p += starter_penalty
        notes.append(f"starters {starter_penalty:.1f} pts")

    weather_penalty = float(game.get("weather_penalty", 0.0))
    if weather_penalty:
        p += weather_penalty
        notes.append(f"weather {weather_penalty:.1f} pts")

    if market_key.startswith("fg"):
        bullpen_penalty = float(game.get("bullpen_penalty", 0.0))
        if bullpen_penalty:
            p += bullpen_penalty
            notes.append(f"bullpen {bullpen_penalty:.1f} pts")

    lineup_penalty = float(game.get("lineup_penalty", 0.0))
    if lineup_penalty:
        p += lineup_penalty
        notes.append(f"lineups {lineup_penalty:.1f} pts")

    return p, notes


def reason_text(selection, edge_pct, penalty_pct, game, extra_note=""):
    chunks = [f"edge {edge_pct:.1f} pts", f"penalty {penalty_pct:.1f} pts"]
    starter_note = game.get("starter_note")
    if starter_note:
        chunks.append(starter_note)
    if game.get("lineup_status"):
        chunks.append(f"{game['lineup_status']}")
    if extra_note:
        chunks.append(extra_note)
    return "; ".join(chunks)


def price_market(name, selection, odds_a, odds_b, p_est, game, market_key, extra_note="", is_alt=False):
    fair = fair_probability(odds_a, odds_b)
    market_hold = hold(odds_a, odds_b)
    market_hold_pct = 100.0 * market_hold
    edge = p_est - fair
    ev = (p_est * odds_a) - 1.0

    penalty, penalty_notes = penalty_for_market(game, market_key, is_alt=is_alt)
    if (not is_alt and market_hold_pct > 6.0) or (is_alt and market_hold_pct > 8.0):
        extra = 1.0 if is_alt else 0.5
        penalty += extra
        penalty_notes.append(f"hold {extra:.1f} pts")

    adjusted_edge = edge - (penalty / 100.0)

    capped = None
    away_tbd = str(game.get("away_starter", "TBD")) == "TBD"
    home_tbd = str(game.get("home_starter", "TBD")) == "TBD"
    if market_key.startswith("f5") and (away_tbd or home_tbd):
        capped = apply_cap(capped, "SKIP")
    elif market_key.startswith("f5") and not game.get("starters_confirmed", False):
        capped = apply_cap(capped, "LEAN")

    if away_tbd or home_tbd:
        capped = apply_cap(capped, "BET")

    if str(game.get("lineup_status", "")).lower() not in {"confirmed", "projected-near-lock"}:
        capped = apply_cap(capped, "STRONG BET")
    if is_alt and market_hold_pct > 8.0:
        capped = apply_cap(capped, "BET")

    major_sample = bool(game.get("away_starter_limited_sample") or game.get("home_starter_limited_sample"))
    major_lineup = str(game.get("lineup_status", "")).lower() == "projected"
    extreme_weather = float(game.get("weather_penalty", 0.0)) >= 1.0

    edge_pct = edge * 100.0
    adjusted_edge_pct = adjusted_edge * 100.0

    if edge <= 0 or ev <= 0:
        tier = "SKIP"
    else:
        tier = classify_tier(adjusted_edge_pct, ev, market_hold_pct, capped, major_sample, major_lineup, extreme_weather)

    return {
        "market": name,
        "selection": selection,
        "odds": odds_a,
        "opp_odds": odds_b,
        "fair": fair,
        "p_est": p_est,
        "edge": edge,
        "penalty": penalty / 100.0,
        "adjusted_edge": adjusted_edge,
        "ev": ev,
        "hold": market_hold,
        "tier": tier,
        "reason": reason_text(selection, edge_pct, penalty, game, extra_note),
        "penalty_notes": penalty_notes,
    }


def handicap_probabilities(game, fg, f5):
    probs = {}
    rl = game["odds"].get("fg_rl")
    if rl:
        probs["fg_home"] = fg["home_minus_1_5"] if rl["home_line"] < 0 else fg["home_plus_1_5"]
        probs["fg_away"] = fg["away_minus_1_5"] if rl["away_line"] < 0 else fg["away_plus_1_5"]
    f5rl = game["odds"].get("f5_rl")
    if f5rl:
        probs["f5_home"] = f5["home_minus_0_5"] if f5rl["home_line"] < 0 else f5["home_plus_0_5"]
        probs["f5_away"] = f5["away_minus_0_5"] if f5rl["away_line"] < 0 else f5["away_plus_0_5"]
    return probs


def evaluate_game(game):
    home9, away9 = joint_probs(game["lambda_home_9"], game["lambda_away_9"])
    home5, away5 = joint_probs(game["lambda_home_5"], game["lambda_away_5"])

    fg = probs_from_distributions(home9, away9)
    f5 = f5_probs(home5, away5)
    odds = game["odds"]
    rl_probs = handicap_probabilities(game, fg, f5)
    candidates = []

    if "fg_ml" in odds:
        candidates.append(price_market("Full Game Moneyline", f"{game['home_team']} ML", odds["fg_ml"]["home"], odds["fg_ml"]["away"], fg["home_ml"], game, "fg_ml"))
        candidates.append(price_market("Full Game Moneyline", f"{game['away_team']} ML", odds["fg_ml"]["away"], odds["fg_ml"]["home"], fg["away_ml"], game, "fg_ml"))

    if "fg_rl" in odds:
        rl = odds["fg_rl"]
        candidates.append(price_market("Full Game Run Line", f"{game['home_team']} {rl['home_line']:+.1f}", rl["home_odds"], rl["away_odds"], rl_probs["fg_home"], game, "fg_rl"))
        candidates.append(price_market("Full Game Run Line", f"{game['away_team']} {rl['away_line']:+.1f}", rl["away_odds"], rl["home_odds"], rl_probs["fg_away"], game, "fg_rl"))

    if "fg_total" in odds:
        line = odds["fg_total"]["line"]
        p_over = over_prob(fg["total_probs"], line)
        p_under = under_prob(fg["total_probs"], line)
        candidates.append(price_market("Full Game Total", f"Over {line}", odds["fg_total"]["over"], odds["fg_total"]["under"], p_over, game, "fg_total"))
        candidates.append(price_market("Full Game Total", f"Under {line}", odds["fg_total"]["under"], odds["fg_total"]["over"], p_under, game, "fg_total"))

    if "f5_ml" in odds:
        candidates.append(price_market("F5 Moneyline", f"{game['home_team']} F5 ML", odds["f5_ml"]["home"], odds["f5_ml"]["away"], f5["home_ml_cond"], game, "f5_ml", extra_note=f"tie after 5: {f5['tie']:.1%}"))
        candidates.append(price_market("F5 Moneyline", f"{game['away_team']} F5 ML", odds["f5_ml"]["away"], odds["f5_ml"]["home"], f5["away_ml_cond"], game, "f5_ml", extra_note=f"tie after 5: {f5['tie']:.1%}"))

    if "f5_rl" in odds:
        rl = odds["f5_rl"]
        candidates.append(price_market("F5 Run Line", f"{game['home_team']} {rl['home_line']:+.1f} F5", rl["home_odds"], rl["away_odds"], rl_probs["f5_home"], game, "f5_rl"))
        candidates.append(price_market("F5 Run Line", f"{game['away_team']} {rl['away_line']:+.1f} F5", rl["away_odds"], rl["home_odds"], rl_probs["f5_away"], game, "f5_rl"))

    if "f5_total" in odds:
        line = odds["f5_total"]["line"]
        p_over = over_prob(f5["total_probs"], line)
        p_under = under_prob(f5["total_probs"], line)
        candidates.append(price_market("F5 Total", f"Over {line} F5", odds["f5_total"]["over"], odds["f5_total"]["under"], p_over, game, "f5_total"))
        candidates.append(price_market("F5 Total", f"Under {line} F5", odds["f5_total"]["under"], odds["f5_total"]["over"], p_under, game, "f5_total"))

    qualified = [c for c in candidates if c["tier"] != "SKIP"]
    ranked = sorted(qualified, key=lambda x: (x["adjusted_edge"], x["ev"]), reverse=True)
    rejected = [c for c in candidates if c["tier"] == "SKIP"]

    return {
        "game": game,
        "qualified": ranked,
        "rejected": rejected,
        "best": ranked[0] if ranked else None,
        "fg": fg,
        "f5": f5,
    }


def fmt_pct(x: float) -> str:
    return f"{100*x:.1f}%"


def fmt_pts(x: float) -> str:
    return f"{100*x:.1f} pts"


def format_start_time(iso_value: str | None) -> str:
    if not iso_value:
        return "TBD"
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso_value


def tier_rank(tier: str) -> int:
    return DISPLAY_TIERS.get(tier, 0)


def render_report(data, results):
    lines = []
    source = data.get("source", {})
    generated = data.get("generated_at_utc")

    lines.append(f"# MLB Daily Model Report - {data['date']}")
    lines.append("")
    lines.append(f"Source: {source.get('provider', 'N/A')} / {source.get('bookmaker', 'N/A')}")
    if generated:
        lines.append(f"Generated: {generated}")
    lines.append("")

    if not results:
        lines.append("No live MLB games were parsed for this run.")
        return "\n".join(lines)

    top_plays = []
    for result in results:
        best = result.get("best")
        if best and tier_rank(best["tier"]) >= tier_rank("BET"):
            top_plays.append((best, result["game"]))
    top_plays.sort(key=lambda x: (tier_rank(x[0]["tier"]), x[0]["adjusted_edge"], x[0]["ev"]), reverse=True)

    if top_plays:
        lines.append("## Top Plays")
        lines.append("")
        for idx, (pick, game) in enumerate(top_plays[:8], 1):
            lines.append(
                f"{idx}. {pick['selection']} @ {pick['odds']:.2f} - {pick['tier']} | {game['away_team']} at {game['home_team']} | Fair {fmt_pct(pick['fair'])} | P(est) {fmt_pct(pick['p_est'])} | Adj Edge {fmt_pts(pick['adjusted_edge'])} | EV {pick['ev']:.3f}"
            )
        lines.append("")

    lines.append("## Game-by-Game")
    lines.append("")
    for result in results:
        game = result["game"]
        start_time = format_start_time(game.get("source_meta", {}).get("start_time"))
        lines.append(f"### {game['away_team']} at {game['home_team']}")
        lines.append(f"- Start: {start_time}")
        lines.append(f"- Venue: {game.get('venue', 'N/A')}")
        lines.append(f"- Starters: {game.get('away_starter', 'TBD')} vs {game.get('home_starter', 'TBD')}")
        if game.get("starter_note"):
            lines.append(f"- Starter handling: {game['starter_note']}")
        lines.append(f"- Team form: {game.get('away_form_note', 'N/A')} | {game.get('home_form_note', 'N/A')}")
        lines.append(f"- Starter model: {game.get('away_pitcher_note', 'N/A')} | {game.get('home_pitcher_note', 'N/A')}")
        lines.append(f"- Lineups: {game.get('lineup_status', 'unknown')}")
        lines.append(f"- Weather: {game.get('weather_note', 'N/A')}")
        lines.append(f"- Bullpen: {game.get('bullpen_note', 'N/A')}")
        lines.append(f"- Blend: {game.get('model_blend_note', 'N/A')}")
        lines.append(f"- Model score (F5): {game['away_team']} {game['lambda_away_5']:.2f} / {game['home_team']} {game['lambda_home_5']:.2f}")
        lines.append(f"- Model score (FG): {game['away_team']} {game['lambda_away_9']:.2f} / {game['home_team']} {game['lambda_home_9']:.2f}")

        if result["qualified"]:
            best = result["best"]
            lines.append(f"- Best play: {best['selection']} @ {best['odds']:.2f} - {best['tier']} | Fair {fmt_pct(best['fair'])} | P(est) {fmt_pct(best['p_est'])} | Adj Edge {fmt_pts(best['adjusted_edge'])} | EV {best['ev']:.3f}")
            lines.append(f"- Why it qualifies: {best['reason']}")
            extras = [c for c in result["qualified"][1:3]]
            for c in extras:
                lines.append(f"- Also qualifies: {c['selection']} @ {c['odds']:.2f} - {c['tier']} | Adj Edge {fmt_pts(c['adjusted_edge'])} | EV {c['ev']:.3f}")
        else:
            lines.append("- Best play: No qualifying selections.")
        lines.append("")

    skipped = data.get("skipped", [])
    if skipped:
        lines.append("## Skipped / Data Issues")
        lines.append("")
        shown = 0
        for row in skipped:
            away = row.get("away") or row.get("away_team") or "Away"
            home = row.get("home") or row.get("home_team") or "Home"
            reason = row.get("reason", "unknown")
            lines.append(f"- {away} at {home}: {reason}")
            shown += 1
            if shown >= 10:
                break
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run the MLB daily report from a slate JSON.")
    parser.add_argument("--input", required=True, help="Path to slate JSON")
    parser.add_argument("--output", default=None, help="Output markdown file path")
    args = parser.parse_args()

    input_path = Path(args.input)
    data = json.loads(input_path.read_text())
    results = [evaluate_game(game) for game in data.get("games", [])]
    report = render_report(data, results)

    output_path = Path(args.output) if args.output else Path("mlb_automation") / f"report_{data['date'].replace('-', '')}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"Report written to {output_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()
