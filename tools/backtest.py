"""Full model backtest against WC2026 results."""
from __future__ import annotations

import json
import numpy as np
from pathlib import Path

from model.bivariate_poisson import load_fit
from model.predict import predict, attack_defense_power

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "docs" / "data" / "history.json"


def run():
    fit = load_fit()
    params = fit.to_dict()
    print(f"Model: {params['model']}, rho: {params.get('rho', 'N/A')}")

    with open(HISTORY) as f:
        hist = json.load(f)

    wc = []
    for hfx in hist["fixtures"]:
        r = hfx.get("result")
        if r:
            wc.append({"home": hfx["home"], "away": hfx["away"],
                        "hg": r["home"], "ag": r["away"]})

    c1x2 = cou = cbtts = 0
    draw_c = draw_t = 0
    ll = 0
    cal = {}

    for m in wc:
        try:
            out = predict(fit, m["home"], m["away"],
                          {"home": 2, "draw": 3.5, "away": 4,
                           "over_2_5": 1.9, "under_2_5": 2.0},
                          neutral=True)
        except (KeyError, Exception):
            continue

        p = out["model_probs"]["1x2"]
        pred = max(p, key=p.get)
        actual = "home" if m["hg"] > m["ag"] else ("away" if m["ag"] > m["hg"] else "draw")
        if pred == actual:
            c1x2 += 1
        if actual == "draw":
            draw_t += 1
            if pred == "draw":
                draw_c += 1
        ll -= np.log(max(p[actual], 1e-8))

        for outcome, prob in p.items():
            b = int(prob * 100 // 10) * 10
            k = f"{b}-{b+10}"
            cal.setdefault(k, {"ps": 0, "as": 0, "n": 0})
            cal[k]["ps"] += prob
            cal[k]["as"] += (1 if outcome == actual else 0)
            cal[k]["n"] += 1

        ou = out["model_probs"]["ou_2_5"]
        total = m["hg"] + m["ag"]
        if ("over" if ou["over"] > ou["under"] else "under") == \
           ("over" if total > 2.5 else "under"):
            cou += 1
        bt = out["model_probs"]["btts"]
        if ("yes" if bt["yes"] > bt["no"] else "no") == \
           ("yes" if m["hg"] > 0 and m["ag"] > 0 else "no"):
            cbtts += 1

    n = len(wc)
    print(f"\n{'='*60}")
    print(f"FINAL: Dixon-Coles + Elo + Benter + DrawInflation + Form")
    print(f"{'='*60}")
    print(f"1X2:  {c1x2}/{n} = {c1x2/n*100:.1f}%")
    print(f"O/U:  {cou}/{n} = {cou/n*100:.1f}%")
    print(f"BTTS: {cbtts}/{n} = {cbtts/n*100:.1f}%")
    if draw_t:
        print(f"Draw: {draw_c}/{draw_t} ({draw_c/draw_t*100:.0f}%)")
    print(f"Log loss: {ll/n:.3f}")

    print(f"\nCalibration:")
    print(f"  {'Bin':>8} {'N':>5} {'Pred':>8} {'Actual':>8} {'Gap':>6}")
    for k in sorted(cal.keys(), key=lambda x: int(x.split("-")[0])):
        v = cal[k]
        if v["n"] < 3:
            continue
        ap = v["ps"] / v["n"] * 100
        aa = v["as"] / v["n"] * 100
        print(f"  {k:>8} {v['n']:>5} {ap:>7.1f}% {aa:>7.1f}% {aa-ap:>+5.1f}%")

    # Monte Carlo
    print(f"\n{'='*60}")
    print(f"MONTE CARLO (10k sims)")
    print(f"{'='*60}")
    rng = np.random.default_rng(42)
    bets = []
    for hfx in hist["fixtures"]:
        for b in hfx.get("bets", []):
            if b.get("won") is None:
                continue
            odds = b.get("odds", 1.0)
            mp = None
            for pk in hfx.get("picks", []):
                if pk.get("market") == b.get("market"):
                    mp = pk.get("model_prob", 50) / 100
                    break
            if mp is None:
                mp = (b.get("edge", 0) / 100) + (1 / odds)
            bets.append({"odds": odds, "prob": min(mp, 0.95), "won": b["won"]})

    print(f"Bets: {len(bets)}")
    won = sum(1 for b in bets if b["won"])
    profit = sum((b["odds"] - 1) if b["won"] else -1 for b in bets)
    print(f"Actual: {won}W-{len(bets)-won}L ROI={profit/len(bets)*100:+.1f}%")

    flat_rois = np.zeros(10000)
    kelly_finals = np.zeros(10000)
    for i in range(10000):
        fp = 0
        bank = 1000.0
        for b in bets:
            bo = b["odds"] - 1
            kf = max(0, min((bo * b["prob"] - (1 - b["prob"])) / bo * 0.25, 0.05))
            if rng.random() < b["prob"]:
                fp += bo
                bank += bank * kf * bo
            else:
                fp -= 1
                bank -= bank * kf
        flat_rois[i] = fp / len(bets) * 100
        kelly_finals[i] = bank

    print(f"\nFlat: median={np.median(flat_rois):+.1f}% mean={np.mean(flat_rois):+.1f}%")
    print(f"  P5={np.percentile(flat_rois,5):+.1f}% P95={np.percentile(flat_rois,95):+.1f}%")
    print(f"  Profitable: {(flat_rois>0).mean()*100:.1f}%")
    print(f"\nKelly (€1000):")
    print(f"  Median=€{np.median(kelly_finals):.0f} ({(np.median(kelly_finals)/1000-1)*100:+.1f}%)")
    print(f"  P5=€{np.percentile(kelly_finals,5):.0f}  P95=€{np.percentile(kelly_finals,95):.0f}")
    print(f"  Profitable: {(kelly_finals>1000).mean()*100:.1f}%")
    print(f"  Ruin (<€500): {(kelly_finals<500).mean()*100:.1f}%")
    print(f"  Double: {(kelly_finals>2000).mean()*100:.1f}%")

    # Attack/defense for WC teams
    print(f"\n{'='*60}")
    print(f"WC TEAM POWER RANKINGS")
    print(f"{'='*60}")
    wc_teams = set()
    for hfx in hist["fixtures"]:
        wc_teams.add(hfx["home"])
        wc_teams.add(hfx["away"])

    powers = []
    for t in wc_teams:
        p = attack_defense_power(fit, t)
        if p:
            powers.append((t, float(p["attack"]), float(p["defense"])))

    print(f"\n  {'Team':20} {'Attack':>8} {'Defense':>8}")
    print(f"  {'-'*38}")
    powers.sort(key=lambda x: -x[1])
    print("  TOP ATTACK:")
    for t, a, d in powers[:10]:
        print(f"  {t:20} {a:+8.3f} {d:+8.3f}")
    powers.sort(key=lambda x: x[2])
    print("\n  TOP DEFENSE:")
    for t, a, d in powers[:10]:
        print(f"  {t:20} {a:+8.3f} {d:+8.3f}")

    # Draw analysis
    print(f"\n{'='*60}")
    print(f"DRAW ANALYSIS")
    print(f"{'='*60}")
    for m in wc:
        actual = "draw" if m["hg"] == m["ag"] else "skip"
        if actual != "draw":
            continue
        try:
            out = predict(fit, m["home"], m["away"],
                          {"home": 2, "draw": 3.5, "away": 4}, neutral=True)
            p = out["model_probs"]["1x2"]
            print(f"  {m['home']:15} {m['hg']}-{m['ag']} {m['away']:15} "
                  f"draw={p['draw']*100:.1f}% "
                  f"form_h={out['form']['home']:.3f} form_a={out['form']['away']:.3f}")
        except Exception:
            pass


if __name__ == "__main__":
    run()
