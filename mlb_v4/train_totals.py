#!/usr/bin/env python3
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

import joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

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
    y = [float(r["total_runs"]) for r in rows]
    return X, y


def split_rows(rows):
    n = len(rows)
    train_end = int(n * 0.70)
    valid_end = int(n * 0.85)
    return rows[:train_end], rows[train_end:valid_end], rows[valid_end:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Train an MLB totals model.")
    parser.add_argument("--input", default=str(config.FEATURES_PATH))
    args = parser.parse_args()

    rows = load_rows(Path(args.input))
    if len(rows) < 200:
        raise RuntimeError("Not enough historical rows to train totals model.")

    train_rows, _, test_rows = split_rows(rows)
    X_train, y_train = to_xy(train_rows)
    X_test, y_test = to_xy(test_rows)

    model = HistGradientBoostingRegressor(max_depth=4, learning_rate=0.05, max_iter=250)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    rmse = math.sqrt(mean_squared_error(y_test, preds))

    metrics = {
        "rows_total": len(rows),
        "rows_train": len(train_rows),
        "rows_test": len(test_rows),
        "mae": round(mae, 6),
        "rmse": round(rmse, 6),
        "feature_columns": FEATURE_COLUMNS,
    }

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, config.MODEL_DIR / "totals_model.joblib")
    (config.MODEL_DIR / "totals_meta.json").write_text(json.dumps(metrics, indent=2))

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
