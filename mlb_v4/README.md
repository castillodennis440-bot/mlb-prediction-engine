# MLB Model V4 Foundation

This is the training/model foundation for a stronger MLB prediction engine.

It is separate from the current `mlb_automation/` email workflow.

## Goal
Build a more independent model that does not rely mostly on sportsbook odds.

## What this version adds
- Historical MLB game collection
- Chronological feature engineering
- A moneyline probability model
- A totals projection model
- A daily scoring script for today's games

## Project structure
- `config.py` — paths and core settings
- `DATA_SCHEMA.md` — schema for raw data, features, and outputs
- `requirements_v4.txt` — Python packages for the V4 model
- `historical_collector.py` — collect historical MLB games from MLB Stats API
- `feature_builder.py` — build chronological team-form features
- `train_moneyline.py` — train and calibrate a moneyline probability model
- `train_totals.py` — train a totals model
- `score_today.py` — score today's MLB slate using trained models

## Recommended workflow
1. Collect historical data
2. Build features
3. Train moneyline model
4. Train totals model
5. Score today's slate
6. Later connect this to the email automation workflow

## Suggested first run order
```bash
python mlb_v4/historical_collector.py --start 2024-03-01 --end 2026-06-24
python mlb_v4/feature_builder.py
python mlb_v4/train_moneyline.py
python mlb_v4/train_totals.py
python mlb_v4/score_today.py --date 2026-06-25
