# Edge — Football Prediction Engine

Dixon-Coles model predictions with edge analysis for FIFA World Cup 2026.

## Features

1. **Picks** — model probability vs market odds, flags value edges per market (1X2, O/U, BTTS). Live odds via The Odds API.
2. **Quiniela optimizer** — tournament-optimized score predictions for fixed-format pools. Maximizes expected points + variance + tiebreak based on your position.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your Odds API key

# Fit the model (~90s)
python -m model.dixon_coles

# Generate predictions
python api/generate.py

# Serve locally
python -m http.server 8000 -d docs
```

## How it works

- **Dixon-Coles** MLE fit on 20k+ international matches with time-decay + tournament weighting
- **Shin de-vig** extracts true probabilities from bookmaker odds
- **Edge = model_prob - devig_prob** — positive edge = model sees value the market doesn't
- **Quiniela optimizer** brute-forces all score combinations against the joint probability matrix, maximizing `E[pts] + λ_var·SD + λ_tie·E[ties]`

## Auto-updates

GitHub Actions runs `api/generate.py` every 30 minutes during the tournament. Fetches live odds + scores, runs model, commits updated `predictions.json`. GitHub Pages serves the static site.

## License & ethics

Predictions are for analysis and entertainment. Not financial advice. The user is responsible for their own decisions.
