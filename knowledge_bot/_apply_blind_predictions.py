"""Apply blind AI-KB predictions to L7-USER-REAL drafts. Predictions live in
knowledge_bot/_blind_predictions.json keyed by case number. Run repeatedly as
more predictions are added."""
import json, os, sys

base = "_knowledge_base/scenario_review_casebook/layer7_real_chart_cases"
pred_path = sys.argv[1] if len(sys.argv) > 1 else "knowledge_bot/_blind_predictions.json"
preds = json.load(open(pred_path, encoding="utf-8"))

applied = []
for num_str, exp in preds.items():
    n = int(num_str)
    p = os.path.join(base, f"L7-USER-REAL-{n:03d}.template.json")
    if not os.path.exists(p):
        print(f"  MISSING {p}", file=sys.stderr)
        continue
    d = json.load(open(p, encoding="utf-8"))
    full = {
        "review_status": exp.get("review_status"),
        "hard_gate_status": exp.get("hard_gate_status"),
        "entry_direction": exp.get("entry_direction"),
        "entry_status": exp.get("entry_status"),
        "best_entry_model": exp.get("best_entry_model"),
        "best_entry_status": exp.get("best_entry_status"),
        "scenario_family": exp.get("scenario_family"),
        "main_level_status": exp.get("main_level_status"),
        "main_level_price": exp.get("main_level_price"),
        "main_level_tolerance": exp.get("main_level_tolerance", 0.01),
        "blocker_items": exp.get("blocker_items", []),
        "manual_review_items": exp.get("manual_review_items", []),
    }
    d["ai_kb_trader_label"]["expected"] = full
    d["ai_kb_trader_label"]["blind_review_notes"] = exp.get("notes", "blind AI-KB review from bars only")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    applied.append(n)

print(f"Applied blind predictions to: {sorted(applied)}")
