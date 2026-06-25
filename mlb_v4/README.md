# MLB Model V4.1 Foundation

This is the stronger model foundation for your MLB prediction engine.

It is separate from the current `mlb_automation/` email workflow.

## Goal
Build a more independent MLB model with:
- historical game data
- team-form features
- starting-pitcher features
- calibrated moneyline probabilities
- totals projections

## What V4.1 adds over V4
- Historical starter game-line extraction from boxscores
- Trailing starter form features
- Better daily scoring inputs for probable starters
- Cleaner separation between model training and email delivery

## Project structure
- `config.py` — core paths and constants
- `DATA_SCHEMA.md` — raw / feature / output schema
- `requirements_v4.txt` — V4.1 Python dependencies
- `historical_collector.py` — collect historical games and starter lines
- `feature_builder.py` — build chronological team + starter features
- `train_moneyline.py` — train calibrated moneyline model
- `train_totals.py` — train totals regression model
- `score_today.py` — score today's slate using trained models

## Recommended workflow
1. Collect historical MLB games
2. Build training features
3. Train moneyline model
4. Train totals model
5. Score today's slate
6. Later connect the scored output into your live email workflow

## Suggested run order
```bash
python mlb_v4/historical_collector.py --start 2024-03-01 --end 2026-06-24
python mlb_v4/feature_builder.py
python mlb_v4/train_moneyline.py
python mlb_v4/train_totals.py
python mlb_v4/score_today.py --date 2026-06-25
