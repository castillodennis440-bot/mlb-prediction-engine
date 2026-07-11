# App Integration Notes

This document explains how the MLB prediction engine connects to the MLB Edge Tracker app.

The app repo is separate from the model repo.

## Repositories

Model repo: mlb-prediction-engine
- Build MLB slate
- Run model workflow
- Generate daily predictions
- Push predictions to Supabase
- Settle results after games finish

App repo: mlb-edge-tracker
- User-facing app
- Supabase Auth login
- Read-only daily predictions
- Results dashboard
- Analytics/history UI

## End-to-End Architecture

Prediction flow:
1. MLB Daily Report workflow runs
2. Build live odds slate
3. Score V4.1 model
4. Merge model predictions into final slate
5. Push predictions to Supabase
6. App displays picks

Settlement flow:
1. MLB games finish
2. MLB Result Settlement workflow runs
3. Fetch final MLB scores
4. Settle pending predictions
5. Insert rows into Supabase results table
6. Update prediction statuses
7. App displays results/ROI/history

## Supabase Project

Supabase is the shared backend between the model repo and the app.

Stores: predictions, results, games, model_runs, model_metrics, profiles.

Normal app users are read-only.
Model workflows write using the secure service role key stored in GitHub Actions secrets.

## Required GitHub Secrets (model repo)

- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY
- ODDSPAPI_API_KEY
- EMAIL_USER
- EMAIL_PASS
- EMAIL_TO

Never expose SUPABASE_SERVICE_ROLE_KEY in frontend code. It belongs only in GitHub Actions secrets or secure backend environments.

## Daily Prediction Workflow

File: .github/workflows/mlb_daily.yml
Name: MLB Daily Report

Steps:
1. Check out repository
2. Set up Python
3. Install dependencies
4. Set report date
5. Build live odds slate
6. Collect historical MLB games
7. Build V4.1 features
8. Train V4.1 moneyline model
9. Train V4.1 totals model
10. Score today's slate
11. Merge V4.1 predictions into live slate
12. Push predictions to Supabase
13. Run daily report
14. Convert report to PDF
15. Send report email
16. Upload report artifact

## Prediction Push Script

Script: mlb_automation/push_to_supabase.py
Input: mlb_automation/final_scoring_slate.json
Output summary: mlb_automation/supabase_push_summary.json

Purpose:
- Read final scoring slate
- Derive model prediction candidates (Poisson from lambdas + odds)
- Convert to app database format
- Insert/update rows in Supabase predictions
- Avoid duplicate predictions
- Keep top positive-EV candidates per game

## Supabase Predictions Table

App reads from public.predictions.

Columns:
- id
- model_version
- game_date
- start_time
- away_team
- home_team
- venue
- market_type
- selection
- line_value
- odds_decimal
- fair_probability
- predicted_probability
- adjusted_edge
- ev
- stake_units
- confidence_tier
- reasoning
- status
- archived
- deleted_at

## Supported Market Types

- Moneyline
- Run Line
- Total
- F5 Winner
- F5 Handicap

Use exact casing.

## Prediction Status Values

- pending
- win
- loss
- push
- void

Daily push inserts pending. Settlement changes status to win/loss/push/void.

## Result Settlement Workflow

File: .github/workflows/mlb_settlement.yml
Name: MLB Result Settlement
Schedule: cron 0 12 * * * plus manual dispatch with settlement_date input.

Purpose:
- Find pending predictions for a date
- Fetch MLB final scores from MLB Stats API
- Match predictions to final games
- Settle win/loss/push outcomes
- Insert rows into Supabase results
- Update predictions.status

## Settlement Script

Script: mlb_automation/settle_supabase_results.py
Output summary: mlb_automation/settlement_summary.json
MLB API: https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=linescore

Supports:
- Moneyline, Run Line, Total, F5 Winner, F5 Handicap settlement
- Reversed home/away matching (flips scores into prediction orientation)
- Team nickname/alias matching in selection_side
- Final score storage
- Profit/loss and ROI calculation

## Supabase Results Table

App reads from public.results.

Columns:
- id
- prediction_id
- final_away_runs
- final_home_runs
- result_status
- settled_at
- profit_loss_units
- roi_impact

## Settlement Logic

Moneyline: win if selected team wins.
Total: over wins if total runs above line, under wins if below, push if equal.
Run Line: selected team score adjusted by line_value.
  Example: Washington Nationals +1.5 wins if Nationals runs + 1.5 > opponent runs.
F5 Winner: uses first five innings score.
F5 Handicap: uses first five innings score plus handicap line.

## Profit/Loss Rules (decimal odds)

- Win: stake * (odds_decimal - 1)
- Loss: -stake
- Push: 0
- Void: 0

## Team Matching Notes

Settlement normalizes official names, nicknames, shortened names, and handles reversed home/away order because model slate order and MLB Stats API order may differ.

Example:
Model prediction: Detroit Tigers vs Houston Astros
MLB API final game: Houston Astros at Detroit Tigers
The script matches reversed order and flips scores.

## App Behavior

Authenticated users can view predictions, results, history, analytics.
Users cannot add, edit, delete, or settle picks.
App auto-refreshes from Supabase every 3 minutes and has a manual Refresh Data button.

## Troubleshooting

Push summary shows prediction_candidates 0:
- final_scoring_slate.json field names changed. Update push_to_supabase.py mapper.

Settlement skipped_no_final high:
- Team names not matching or games not final. Check normalize_team and match_game logs.

Settlement skipped_unsettleable high:
- selection_side could not detect team, or market/line missing. Improve selection_side.

IndentationError on line 1:
- Remove any spaces before the first import line.

App shows no data:
- Confirm predictions rows exist for today, RLS read policy allows archived=false and deleted_at is null, and user is logged in.

## Summary

The model workflow publishes predictions.
The settlement workflow publishes results.
Supabase stores both.
The app displays everything to users in a read-only, mobile-friendly analytics dashboard.
