# MLB V4 Data Schema

## 1) Raw historical games
File: `mlb_v4/data/raw/games.jsonl`

One JSON object per game.

Fields:
- `game_pk`
- `game_date`
- `start_time`
- `status`
- `venue`
- `away_team`
- `home_team`
- `away_team_id`
- `home_team_id`
- `away_runs`
- `home_runs`
- `away_starter`
- `home_starter`
- `away_starter_id`
- `home_starter_id`

## 2) Engineered feature rows
File: `mlb_v4/data/features/game_features.csv`

Columns:
- identifiers
  - `game_date`
  - `game_pk`
  - `away_team`
  - `home_team`
- features
  - `home_last10_win_pct`
  - `away_last10_win_pct`
  - `home_last10_rs`
  - `away_last10_rs`
  - `home_last10_ra`
  - `away_last10_ra`
  - `home_last5_home_win_pct`
  - `away_last5_away_win_pct`
  - `home_rest_days`
  - `away_rest_days`
  - `home_form_index`
  - `away_form_index`
- targets
  - `home_win`
  - `total_runs`
  - `home_runs`
  - `away_runs`

## 3) Trained model artifacts
Folder: `mlb_v4/models/`

Expected files:
- `moneyline_model.joblib`
- `moneyline_calibrator.joblib`
- `moneyline_meta.json`
- `totals_model.joblib`
- `totals_meta.json`

## 4) Daily predictions output
File: `mlb_v4/output/today_predictions.json`

Fields per game:
- `game_date`
- `away_team`
- `home_team`
- `start_time`
- `venue`
- `away_starter`
- `home_starter`
- `home_win_prob_raw`
- `home_win_prob_calibrated`
- `away_win_prob_calibrated`
- `total_runs_pred`
- `suggested_home_score`
- `suggested_away_score`
