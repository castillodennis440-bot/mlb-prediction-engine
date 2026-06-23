#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path

MAX_RUNS = 20

def poisson_pmf(k: int, lam: float) -> float:
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
    p_away_plus_1_5 = 0.0
    total_probs = {}

    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            p = ph * pa
            total = h + a
            total_probs[total] = total_probs.get(total, 0.0) + p
            if h > a:
                p_home += p
                if h - a >= 2:
                    p_home_by_2 += p
            elif a > h:
                p_away += p
            else:
                p_tie += p

            if h - a >= -1:
                p_away_plus_1_5 += p

    return {
        "home_win_9": p_home,
        "away_win_9": p_away,
        "tie_9": p_tie,
        "home_ml": p_home + 0.5 * p_tie,
        "away_ml": p_away + 0.5 * p_tie,
        "home_minus_1_5": p_home_by_2,
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

def classify_tier(adjusted_edge: float, ev: float, hold_pct: float):
    if adjusted_edge >= 8.0 and ev >= 0.08 and hold_pct <= 5.0:
        return "BANKER"
    if adjusted_edge >= 5.0 and ev >= 0.05 and hold_pct <= 6.0:
        return "STRONG BET"
    if adjusted_edge >= 3.0 and ev >= 0.03:
        return "BET"
    if adjusted_edge >= 1.5 and ev > 0:
        return "LEAN"
    return "SKIP"

def price_market(name, selection, odds_a, odds_b, p_est):
    fair = fair_probability(odds_a, odds_b)
    market_hold = hold(odds_a, odds_b)
    edge = p_est - fair
    ev = (p_est * odds_a) - 1.0
    adjusted_edge = (edge * 100.0)

    tier = "SKIP" if edge <= 0 or ev <= 0 else classify_tier(adjusted_edge, ev, market_hold * 100.0)

    return {
        "market": name,
        "selection": selection,
        "odds": odds_a,
        "fair": fair,
        "p_est": p_est,
        "edge": edge,
        "adjusted_edge": edge,
        "ev": ev,
        "tier": tier,
    }

def evaluate_game(game):
    home9, away9 = joint_probs(game["lambda_home_9"], game["lambda_away_9"])
    home5, away5 = joint_probs(game["lambda_home_5"], game["lambda_away_5"])

    fg = probs_from_distributions(home9, away9)
    f5 = f5_probs(home5, away5)
    odds = game["odds"]

    candidates = []

    candidates.append(price_market(
        "Full Game Moneyline",
        f'{game["home_team"]} ML',
        odds["fg_ml"]["home"],
        odds["fg_ml"]["away"],
        fg["home_ml"]
    ))
    candidates.append(price_market(
        "Full Game Moneyline",
        f'{game["away_team"]} ML',
        odds["fg_ml"]["away"],
        odds["fg_ml"]["home"],
        fg["away_ml"]
    ))

    line = odds["fg_total"]["line"]
    p_over = over_prob(fg["total_probs"], line)
    p_under = under_prob(fg["total_probs"], line)

    candidates.append(price_market(
        "Full Game Total",
        f"Over {line}",
        odds["fg_total"]["over"],
        odds["fg_total"]["under"],
        p_over
    ))
    candidates.append(price_market(
        "Full Game Total",
        f"Under {line}",
        odds["fg_total"]["under"],
        odds["fg_total"]["over"],
        p_under
    ))

    qualified = [c for c in candidates if c["tier"] != "SKIP"]
    ranked = sorted(qualified, key=lambda x: (x["adjusted_edge"], x["ev"]), reverse=True)

    return {
        "game": game,
        "qualified": ranked,
        "best": ranked[0] if ranked else None,
    }

def render_report(data, results):
    lines = []
    lines.append(f'# MLB Daily Model Report — {data["date"]}')
    lines.append("")
    if not results:
        lines.append("No live MLB games were parsed from Betano for this run.")
        return "/n". join(lines)
    for result in results:
        game = result["game"]
        lines.append(f'## {game["away_team"]} at {game["home_team"]}')
        lines.append("")
        lines.append(f'- Venue: {game["venue"]}')
        lines.append(f'- Model λ (FG): {game["away_team"]} {game["lambda_away_9"]:.2f} / {game["home_team"]} {game["lambda_home_9"]:.2f}')
        lines.append("")

        if result["qualified"]:
            for c in result["qualified"]:
                lines.append(
                    f'- {c["selection"]} @ {c["odds"]:.2f} | Fair {100*c["fair"]:.1f}% | P(est) {100*c["p_est"]:.1f}% | Edge {100*c["edge"]:.1f} pts | EV {c["ev"]:.3f} | {c["tier"]}'
                )
        else:
            lines.append('- No qualifying selections.')
        lines.append("")

    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text())
    results = [evaluate_game(game) for game in data.get("games", [])]
    report = render_report(data, results)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(report)

if __name__ == "__main__":
    main()
