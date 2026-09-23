import json, os, sys

base = "_knowledge_base/scenario_review_casebook/layer7_real_chart_cases"
nums = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else list(range(24, 36))

for n in nums:
    p = os.path.join(base, f"L7-USER-REAL-{n:03d}.template.json")
    d = json.load(open(p, encoding="utf-8"))
    bars = d["bars"]["context"]
    sym = d["symbol"]
    period = d["title"].split()[-1]
    print(f"\n===== L7-USER-REAL-{n:03d}  {sym}  ({period})  D1 bars={len(bars)} =====")
    for b in bars:
        dt = b["open_time"][:10]
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        print(f"{dt}  O={o:>11.5g}  H={h:>11.5g}  L={l:>11.5g}  C={c:>11.5g}")
