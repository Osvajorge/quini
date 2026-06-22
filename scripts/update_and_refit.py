"""Download latest martj42 results, merge known WC 2026 scores, refit if new data.

Run from project root:
    python scripts/update_and_refit.py
"""
from __future__ import annotations
import csv
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS_CSV = Path("data/results.csv")
PREDICTIONS_JSON = Path("docs/data/predictions.json")
REFIT_STATE = Path("data/refit_state.json")
MARTJ42_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
WC26_START = "2026-06-11"

# predictions.json team names → martj42 CSV team names
TO_CSV = {
    "USA": "United States",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "DR Congo": "DR Congo",
    "Ivory Coast": "Ivory Coast",
    "South Korea": "South Korea",
    "Saudi Arabia": "Saudi Arabia",
    "New Zealand": "New Zealand",
    "South Africa": "South Africa",
    "Cape Verde": "Cape Verde",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
}


def download_results() -> None:
    print(f"Downloading results.csv from martj42...")
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(MARTJ42_URL, RESULTS_CSV)
    with open(RESULTS_CSV) as f:
        n = sum(1 for _ in f) - 1  # subtract header
    print(f"  {n:,} rows downloaded")


def load_score_lookup() -> dict[tuple[str, str], tuple[int, int]]:
    """Build (home_csv_name, away_csv_name) → (h_score, a_score) from predictions.json."""
    if not PREDICTIONS_JSON.exists():
        return {}
    d = json.load(open(PREDICTIONS_JSON))
    lookup: dict[tuple[str, str], tuple[int, int]] = {}
    for f in d.get("fixtures", []):
        if not f.get("completed") or f.get("actual_home") is None:
            continue
        h = TO_CSV.get(f["home"], f["home"])
        a = TO_CSV.get(f["away"], f["away"])
        lookup[(h, a)] = (int(f["actual_home"]), int(f["actual_away"]))
        # Also store reversed in case CSV has teams swapped (home/away can differ)
        lookup[(a, h)] = (int(f["actual_away"]), int(f["actual_home"]))
    return lookup


def update_scores(score_lookup: dict) -> int:
    """Fill NA scores in results.csv from score_lookup. Returns count updated."""
    rows = []
    updated = 0
    with open(RESULTS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row["home_score"] in ("", "NA") or row["away_score"] in ("", "NA"):
                key = (row["home_team"], row["away_team"])
                if key in score_lookup:
                    hs, as_ = score_lookup[key]
                    row["home_score"] = str(hs)
                    row["away_score"] = str(as_)
                    updated += 1
            rows.append(row)

    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return updated


def count_completed_wc26() -> int:
    """Count WC 2026 group stage rows with real scores in results.csv."""
    count = 0
    with open(RESULTS_CSV) as f:
        for row in csv.DictReader(f):
            if (row["date"] >= WC26_START
                    and row["tournament"] == "FIFA World Cup"
                    and row["home_score"] not in ("", "NA")
                    and row["away_score"] not in ("", "NA")):
                count += 1
    return count


def needs_refit(current_count: int) -> bool:
    from model.dixon_coles import FIT_PATH
    if not FIT_PATH.exists():
        return True
    if not REFIT_STATE.exists():
        return True
    state = json.load(open(REFIT_STATE))
    return current_count > state.get("completed_wc26_count", 0)


def save_refit_state(count: int) -> None:
    REFIT_STATE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "completed_wc26_count": count,
        "refitted_at": datetime.now(timezone.utc).isoformat(),
    }
    json.dump(state, open(REFIT_STATE, "w"), indent=2)


def run_refit() -> None:
    from model.data_loader import load_matches
    from model.dixon_coles import fit, save_fit

    df = load_matches()
    wc26 = df[(df["tournament"] == "FIFA World Cup") & (df["date"] >= "2026-06-11")]
    print(f"  Loaded {len(df):,} matches · WC 2026 group stage: {len(wc26)}")
    fit_obj = fit(df, verbose=True)
    save_fit(fit_obj)
    print(f"  Saved fit.pkl · log-L={fit_obj.log_likelihood:.1f} · γ={fit_obj.gamma:.4f}")


def main() -> None:
    download_results()

    score_lookup = load_score_lookup()
    updated = update_scores(score_lookup)
    print(f"Scores merged from predictions.json: {updated} filled")

    count = count_completed_wc26()
    print(f"Completed WC 2026 matches in CSV: {count}")

    if needs_refit(count):
        print(f"Refit needed (new matches since last fit)...")
        run_refit()
        save_refit_state(count)
        print("Refit done.")
    else:
        print("No refit needed (model up to date).")


if __name__ == "__main__":
    main()
