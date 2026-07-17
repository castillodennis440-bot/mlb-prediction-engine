"""
MLB Edge Tracker — Model Quality Metrics Computer

Runs after settlement (or on demand) and writes one row per model version
into public.model_metrics so the app's Model Quality tab shows real numbers.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests


def log(msg):
    print(f"[metrics] {msg}", flush=True)


def to_date(d):
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    return date.today()


def pnl_units(pred, result_status):
    stake = float(pred.get("stake_units") or 1.0)
    odds = float(pred.get("odds_decimal") or 0)
    if result_status == "win":
        return stake * (odds - 1.0)
    if result_status == "loss":
        return -stake
    return 0.0


def letter_grade(brier, roi, cal_err):
    score = 100
    if brier > 0.25: score -= 25
    elif brier > 0.22: score -= 15
    elif brier > 0.20: score -= 8
    elif brier <= 0.18: score += 5
    if cal_err > 0.15: score -= 20
    elif cal_err > 0.10: score -= 12
    elif cal_err > 0.06: score -= 5
    elif cal_err <= 0.04: score += 5
    if roi > 10: score += 10
    elif roi > 5: score += 6
    elif roi > 0: score += 3
    elif roi < -5: score -= 15
    elif roi < -2: score -= 7
    score = max(0, min(100, score))
    if score >= 93: return "A+"
    if score >= 88: return "A"
    if score >= 82: return "A-"
    if score >= 77: return "B+"
    if score >= 72: return "B"
    if score >= 67: return "B-"
    if score >= 62: return "C+"
    if score >= 55: return "C"
    if score >= 48: return "C-"
    if score >= 40: return "D+"
    if score >= 33: return "D"
    return "D-"


def get_supabase():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return (url, key)


def fetch_predictions_with_results(sb, model_version=None):
    url, key = sb
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    params = {
        "select": ("id,model_version,game_date,market_type,selection,line_value,"
                   "odds_decimal,fair_probability,predicted_probability,stake_units,"
                   "confidence_tier,status,archived,"
                   "results(id,result_status,profit_loss_units,final_away_runs,final_home_runs,settled_at)"),
        "archived": "eq.false",
    }
    if model_version:
        params["model_version"] = f"eq.{model_version}"
    res = requests.get(f"{url}/rest/v1/predictions", headers=headers, params=params, timeout=30)
    res.raise_for_status()
    data = res.json()
    data = [r for r in data if r.get("deleted_at") is None]

    rows = []
    for p in data:
        res_list = p.get("results") or []
        r = res_list[0] if res_list else None
        rows.append({
            "id": p.get("id"),
            "model_version": p.get("model_version"),
            "game_date": to_date(p.get("game_date")),
            "market_type": p.get("market_type"),
            "selection": p.get("selection"),
            "odds_decimal": p.get("odds_decimal"),
            "predicted_probability": p.get("predicted_probability"),
            "fair_probability": p.get("fair_probability"),
            "stake_units": p.get("stake_units") or 1.0,
            "confidence_tier": p.get("confidence_tier"),
            "status": p.get("status"),
            "result_status": r.get("result_status") if r else None,
            "profit_loss_units": r.get("profit_loss_units") if r else None,
        })
    return rows


def compute_metrics(rows, as_of, model_version):
    settled = [r for r in rows if r["status"] in ("win", "loss", "push", "void")]
    graded = [r for r in rows if r["status"] in ("win", "loss")]
    pending = [r for r in rows if r["status"] == "pending"]

    wins = sum(1 for r in settled if r["status"] == "win")
    losses = sum(1 for r in settled if r["status"] == "loss")
    pushes = sum(1 for r in settled if r["status"] == "push")
    voids = sum(1 for r in settled if r["status"] == "void")

    total_stake = sum(float(r.get("stake_units") or 1.0) for r in settled)
    total_pl = 0.0
    for r in settled:
        pl = r.get("profit_loss_units")
        if pl is None:
            pl = pnl_units(r, r["status"])
        total_pl += float(pl)
    roi_all = (total_pl / total_stake * 100.0) if total_stake > 0 else 0.0

    def roi_window(days):
        start = as_of - timedelta(days=days)
        w = [r for r in settled if to_date(r["game_date"]) >= start]
        stake_w = sum(float(x.get("stake_units") or 1.0) for x in w)
        pl_w = 0.0
        for x in w:
            pl = x.get("profit_loss_units")
            if pl is None:
                pl = pnl_units(x, x["status"])
            pl_w += float(pl)
        return (pl_w / stake_w * 100.0) if stake_w > 0 else 0.0

    roi_7d = roi_window(7)
    roi_30d = roi_window(30)
    win_rate_overall = (wins / len(graded) * 100.0) if graded else 0.0

    brier_values = []
    logloss_values = []
    calibration_points = []
    for r in graded:
        pp_raw = r.get("predicted_probability")
        if pp_raw is None:
            continue
        p = float(pp_raw)
        if p > 1:
            p = p / 100.0
        p = max(0.01, min(0.99, p))
        target = 1.0 if r["status"] == "win" else 0.0
        brier_values.append((p - target) ** 2)
        logloss_values.append(-(target * math.log(p) + (1 - target) * math.log(1 - p)))
        calibration_points.append((p, target))

    brier = sum(brier_values) / len(brier_values) if brier_values else None
    log_loss_val = sum(logloss_values) / len(logloss_values) if logloss_values else None

    buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    cal_errors = []
    cal_buckets_out = []
    for lo, hi in buckets:
        in_b = [(p, t) for p, t in calibration_points
                if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not in_b:
            cal_buckets_out.append({
                "bucket": f"{int(lo*100)}-{int(hi*100)}%",
                "count": 0, "avg_predicted": None,
                "actual_win_rate": None, "error": None,
            })
            continue
        avg_p = sum(p for p, t in in_b) / len(in_b)
        win_r = sum(t for p, t in in_b) / len(in_b)
        err = abs(avg_p - win_r)
        cal_errors.append(err * len(in_b))
        cal_buckets_out.append({
            "bucket": f"{int(lo*100)}-{int(hi*100)}%",
            "count": len(in_b),
            "avg_predicted": round(avg_p, 4),
            "actual_win_rate": round(win_r, 4),
            "error": round(err, 4),
        })
    ece = (sum(cal_errors) / len(calibration_points)) if calibration_points else None

    tiers = ["High", "Medium", "Value"]
    tier_perf = {}
    tier_roi = {}
    for tier in tiers:
        group_all = [r for r in settled if r.get("confidence_tier") == tier]
        group_graded = [r for r in graded if r.get("confidence_tier") == tier]
        wg = sum(1 for r in group_graded if r["status"] == "win")
        stake_g = sum(float(r.get("stake_units") or 1.0) for r in group_all)
        pl_g = 0.0
        for r in group_all:
            pl = r.get("profit_loss_units")
            if pl is None:
                pl = pnl_units(r, r["status"])
            pl_g += float(pl)
        tier_perf[tier] = {
            "count": len(group_all),
            "wins": wg,
            "graded": len(group_graded),
            "win_rate": round((wg / len(group_graded) * 100.0), 2) if group_graded else None,
            "profit_units": round(pl_g, 3),
        }
        tier_roi[tier] = round((pl_g / stake_g * 100.0), 2) if stake_g > 0 else 0.0

    markets = ["Moneyline", "Run Line", "Total", "F5 Winner", "F5 Handicap"]
    market_perf = {}
    for m in markets:
        group_all = [r for r in settled if r.get("market_type") == m]
        group_graded = [r for r in graded if r.get("market_type") == m]
        wm = sum(1 for r in group_graded if r["status"] == "win")
        stake_m = sum(float(r.get("stake_units") or 1.0) for r in group_all)
        pl_m = 0.0
        for r in group_all:
            pl = r.get("profit_loss_units")
            if pl is None:
                pl = pnl_units(r, r["status"])
            pl_m += float(pl)
        market_perf[m] = {
            "count": len(group_all),
            "wins": wm,
            "graded": len(group_graded),
            "win_rate": round((wm / len(group_graded) * 100.0), 2) if group_graded else None,
            "profit_units": round(pl_m, 3),
            "roi": round((pl_m / stake_m * 100.0), 2) if stake_m > 0 else 0.0,
        }

    markets_with_data = [(n, d) for n, d in market_perf.items() if d["graded"] and d["graded"] >= 1]
    if markets_with_data:
        best = max(markets_with_data, key=lambda kv: kv[1]["roi"])
        worst = min(markets_with_data, key=lambda kv: kv[1]["roi"])
        best_market = best[0]
        worst_market = worst[0]
    else:
        best_market = None
        worst_market = None

    brier_for_grade = brier if brier is not None else 0.25
    cal_for_grade = ece if ece is not None else 0.20
    grade = letter_grade(brier_for_grade, roi_all, cal_for_grade)

    return {
        "model_version": model_version,
        "computed_for_date": as_of.isoformat(),
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "period_start": (as_of - timedelta(days=30)).isoformat(),
        "period_end": as_of.isoformat(),
        "total_picks": len(rows),
        "settled_count": len(settled),
        "pending_count": len(pending),
        "win_count": wins,
        "loss_count": losses,
        "push_count": pushes,
        "void_count": voids,
        "win_rate_overall": round(win_rate_overall, 2),
        "brier_score": round(brier, 4) if brier is not None else None,
        "log_loss": round(log_loss_val, 4) if log_loss_val is not None else None,
        "calibration_error": round(ece, 4) if ece is not None else None,
        "accuracy": round(win_rate_overall / 100.0, 4) if graded else None,
        "roi_7d": round(roi_7d, 2),
        "roi_30d": round(roi_30d, 2),
        "roi_all_time": round(roi_all, 2),
        "total_stake_units": round(total_stake, 3),
        "total_profit_units": round(total_pl, 3),
        "win_rate_high": tier_perf["High"]["win_rate"],
        "win_rate_medium": tier_perf["Medium"]["win_rate"],
        "win_rate_value": tier_perf["Value"]["win_rate"],
        "roi_high": tier_roi["High"],
        "roi_medium": tier_roi["Medium"],
        "roi_value": tier_roi["Value"],
        "market_performance": market_perf,
        "confidence_performance": tier_perf,
        "calibration_buckets": cal_buckets_out,
        "best_market": best_market,
        "worst_market": worst_market,
        "metric_grade": grade,
    }


def upsert_metrics(sb, row):
    url, key = sb
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    res = requests.post(
        f"{url}/rest/v1/model_metrics", headers=headers, json=[row], timeout=30,
    )
    if res.status_code == 409:
        get_res = requests.get(
            f"{url}/rest/v1/model_metrics", headers=headers,
            params={
                "model_version": f"eq.{row['model_version']}",
                "computed_for_date": f"eq.{row['computed_for_date']}",
                "select": "id",
            },
            timeout=30,
        )
        existing = get_res.json()
        if existing:
            rid = existing[0]["id"]
            requests.patch(
                f"{url}/rest/v1/model_metrics?id=eq.{rid}",
                headers={**headers, "Prefer": "return=representation"},
                json=row, timeout=30,
            ).raise_for_status()
    else:
        res.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()

    as_of = to_date(args.as_of) if args.as_of else date.today()
    log(f"Computing model metrics as of {as_of}")

    sb = get_supabase()
    rows = fetch_predictions_with_results(sb, args.model_version)
    log(f"Fetched {len(rows)} prediction rows from Supabase")

    versions = sorted({r["model_version"] for r in rows if r.get("model_version")}) or ["v4.1"]
    if args.model_version:
        versions = [args.model_version]

    summaries = []
    for mv in versions:
        mv_rows = [r for r in rows if r.get("model_version") == mv]
        if not mv_rows:
            log(f"No rows for {mv}; skipping")
            continue
        metrics = compute_metrics(mv_rows, as_of, mv)
        upsert_metrics(sb, metrics)
        summaries.append(metrics)
        log("=" * 60)
        log(f"Model version: {mv}")
        log(f"Grade: {metrics['metric_grade']}")
        log(f"Total picks: {metrics['total_picks']}  Settled: {metrics['settled_count']}  Pending: {metrics['pending_count']}")
        log(f"Win/loss/push/void: {metrics['win_count']}/{metrics['loss_count']}/{metrics['push_count']}/{metrics['void_count']}")
        log(f"Win rate (graded): {metrics['win_rate_overall']}%")
        log(f"Brier: {metrics['brier_score']}  LogLoss: {metrics['log_loss']}  CalE: {metrics['calibration_error']}")
        log(f"ROI 7d / 30d / all: {metrics['roi_7d']}% / {metrics['roi_30d']}% / {metrics['roi_all_time']}%")
        log(f"Profit: {metrics['total_profit_units']}u on {metrics['total_stake_units']}u stake")
        log(f"Best market: {metrics['best_market']}  Worst market: {metrics['worst_market']}")

    out_path = Path(__file__).parent / "model_metrics_summary.json"
    with open(out_path, "w") as f:
        json.dump({"as_of": as_of.isoformat(), "metrics": summaries}, f, indent=2)
    log(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyError as e:
        log(f"Missing environment variable: {e}")
        sys.exit(2)
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
