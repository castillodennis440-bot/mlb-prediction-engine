#!/usr/bin/env python3
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import joblib
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

import config

FEATURE_COLUMNS = [
    "home_last10_win_pct",
    "away_last10_win_pct",
    "home_last10_rs",
    "away_last10_rs",
    "home_last10_ra",
    "away_last10_ra",
    "home_last5_home_win_pct",
    "away_last5_away_win_pct",
    "home_rest_days",
    "away_rest_days",
    "home_form_index",
    "away_form_index",
    "home_pitcher_last5_era",
    "away_pitcher_last5_era",
    "home_pitcher_last5_whip",
    "away_pitcher_last5_whip",
    "home_pitcher_last5_k9",
    "away_pitcher_last5_k9",
    "home_pitcher_starts_hist",
    "away_pitcher_starts_hist",
    "home_pitcher_limited_sample",
    "away_pitcher_limited_sample",
]


def load_rows(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["_date"] = datetime.strptime(row["game_date"], "%Y-%m-%d")
            rows.append(row)
    rows.sort(key=lambda x: x["_date"])
    return rows


def to_xy(rows):
    X = [[float(r[c]) for c in FEATURE_COLUMNS] for r in rows]
    y = [int(r["home_win"]) for r in rows]
    return X, y


def split_rows(rows):
    n = len(rows)
    train_end = int(n * 0.70)
    valid_end = int(n * 0.85)
    return rows[:train_end], rows[train_end:valid_end], rows[valid_end:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a calibrated MLB moneyline model.")
    parser.add_argument("--input", default=str(config.FEATURES_PATH))
    args = parser.parse_args()

    rows = load_rows(Path(args.input))
    if len(rows) < 200:
        raise RuntimeError("Not enough historical rows to train moneyline model.")

    train_rows, valid_rows, test_rows = split_rows(rows)
    X_train, y_train = to_xy(train_rows)
    X_valid, y_valid = to_xy(valid_rows)
    X_test, y_test = to_xy(test_rows)

    model = LogisticRegression(max_iter=3000)
    model.fit(X_train, y_train)

    valid_raw = model.predict_proba(X_valid)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(valid_raw, y_valid)

    test_raw = model.predict_proba(X_test)[:, 1]
    test_cal = calibrator.predict(test_raw)
    test_pred = [1 if p >= 0.5 else 0 for p in test_cal]

    metrics = {
        "rows_total": len(rows),
        "rows_train": len(train_rows),
        "rows_valid": len(valid_rows),
        "rows_test": len(test_rows),
        "log_loss": round(log_loss(y_test, test_cal), 6),
        "brier": round(brier_score_loss(y_test, test_cal), 6),
        "accuracy": round(accuracy_score(y_test, test_pred), 6),
        "feature_columns": FEATURE_COLUMNS,
    }

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, config.MODEL_DIR / "moneyline_model.joblib")
    joblib.dump(calibrator, config.MODEL_DIR / "moneyline_calibrator.joblib")
    (config.MODEL_DIR / "moneyline_meta.json").write_text(json.dumps(metrics, indent=2))

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
