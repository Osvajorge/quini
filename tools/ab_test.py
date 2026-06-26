"""A/B test: v2 baseline vs v3+ model components."""
from __future__ import annotations

import json
import numpy as np
from pathlib import Path

import model.predict as pred
from model.bivariate_poisson import load_fit

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "docs" / "data" / "history.json"

DUMMY_ODDS = {"home": 2, "draw": 3.5, "away": 4,
              "over_2_5": 1.9, "under_2_5": 2.0,
              "btts_yes": 2.1, "btts_no": 1.75}


def eval_model(fit, wc, label, draw_infl, form_wt, benter_a, benter_b, bayes_wt):
    orig_di = pred.DRAW_INFLATION
    orig_fw = pred.FORM_WEIGHT
    orig_ba = pred.BENTER_ALPHA
    orig_bb = pred.BENTER_BETA
    orig_bw = pred.BAYESIAN_UPDATE_WEIGHT

    pred.DRAW_INFLATION = draw_infl
    pred.FORM_WEIGHT = form_wt
    pred.BENTER_ALPHA = benter_a
    pred.BENTER_BETA = benter_b
    pred.BAYESIAN_UPDATE_WEIGHT = bayes_wt
    pred._FORM_CACHE = None
    pred._BAYES_CACHE = None

    c1x2 = cou = cbtts = 0
    draw_c = draw_t = 0
    ll = 0.0

    for m in wc:
        try:
            out = pred.predict(fit, m["home"], m["away"], DUMMY_ODDS, neutral=True)
        except Exception:
            continue
        p = out["model_probs"]["1x2"]
        best = max(p, key=p.get)
        actual = "home" if m["hg"] > m["ag"] else ("away" if m["ag"] > m["hg"] else "draw")
        if best == actual:
            c1x2 += 1
        if actual == "draw":
            draw_t += 1
            if best == "draw":
                draw_c += 1
        ll -= np.log(max(p[actual], 1e-8))

        ou = out["model_probs"]["ou_2_5"]
        total = m["hg"] + m["ag"]
        if ("over" if ou["over"] > ou["under"] else "under") == \
           ("over" if total > 2.5 else "under"):
            cou += 1
        bt = out["model_probs"]["btts"]
        if ("yes" if bt["yes"] > bt["no"] else "no") == \
           ("yes" if m["hg"] > 0 and m["ag"] > 0 else "no"):
            cbtts += 1

    pred.DRAW_INFLATION = orig_di
    pred.FORM_WEIGHT = orig_fw
    pred.BENTER_ALPHA = orig_ba
    pred.BENTER_BETA = orig_bb
    pred.BAYESIAN_UPDATE_WEIGHT = orig_bw
    pred._FORM_CACHE = None
    pred._BAYES_CACHE = None

    n = len(wc)
    return {
        "label": label,
        "1x2": c1x2, "ou": cou, "btts": cbtts,
        "draw": draw_c, "draw_t": draw_t,
        "ll": ll / n, "n": n,
    }


def run():
    fit = load_fit()
    with open(HISTORY) as f:
        hist = json.load(f)

    wc = []
    for hfx in hist["fixtures"]:
        r = hfx.get("result")
        if r:
            wc.append({"home": hfx["home"], "away": hfx["away"],
                        "hg": r["home"], "ag": r["away"]})

    #                    label                       draw  form  α     β    bayes
    configs = [
        ("v2 BASELINE",                              0.0,  0.0, 1.0, 0.0,  0.0),
        ("+ Form 10%",                               0.0, 0.10, 1.0, 0.0,  0.0),
        ("+ Bayes 15%",                              0.0,  0.0, 1.0, 0.0,  0.15),
        ("+ Form + Bayes",                           0.0, 0.10, 1.0, 0.0,  0.15),
        ("+ Draw 3% + Form + Bayes",                0.03, 0.10, 1.0, 0.0,  0.15),
        ("v3 FULL (all)",                            0.03, 0.10, 0.60, 0.40, 0.15),
        # Ablation: try stronger Bayes
        ("+ Form + Bayes 25%",                       0.0, 0.10, 1.0, 0.0,  0.25),
        ("+ Form 15% + Bayes 15%",                   0.0, 0.15, 1.0, 0.0,  0.15),
    ]

    print(f"{'Config':35} {'1X2':>6} {'O/U':>6} {'BTTS':>6} {'Draw':>8} {'LogLoss':>8}")
    print("-" * 75)

    results = []
    for label, di, fw, ba, bb, bw in configs:
        r = eval_model(fit, wc, label, di, fw, ba, bb, bw)
        results.append(r)
        n = r["n"]
        draw_s = f"{r['draw']}/{r['draw_t']}" if r["draw_t"] else "—"
        print(f"  {r['label']:33} {r['1x2']/n*100:5.1f}% {r['ou']/n*100:5.1f}% "
              f"{r['btts']/n*100:5.1f}% {draw_s:>8} {r['ll']:7.3f}")

    base = results[0]
    best_ll = min(results, key=lambda r: r["ll"])
    print(f"\n  DELTA v2 → best ({best_ll['label']}):")
    n = base["n"]
    dll = best_ll["ll"] - base["ll"]
    d1x2 = (best_ll["1x2"] - base["1x2"]) / n * 100
    dou = (best_ll["ou"] - base["ou"]) / n * 100
    print(f"    1X2:  {d1x2:+.1f}%")
    print(f"    O/U:  {dou:+.1f}%")
    print(f"    LogL: {dll:+.4f} ({dll/base['ll']*100:+.1f}%)")

    print(f"\n  RANKING by log loss:")
    by_ll = sorted(results, key=lambda r: r["ll"])
    for i, r in enumerate(by_ll):
        n = r["n"]
        delta = r["ll"] - base["ll"]
        print(f"    {i+1}. {r['label']:33} LL={r['ll']:.4f} ({delta:+.4f}) "
              f"1X2={r['1x2']/n*100:.1f}% O/U={r['ou']/n*100:.1f}%")


if __name__ == "__main__":
    run()
