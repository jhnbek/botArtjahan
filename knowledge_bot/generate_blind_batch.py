#!/usr/bin/env python3
"""
Generate 12 fresh real-case drafts (L7-USER-REAL-024..035) for TRUE blinded review.
Unlike autofill_batch_kb_labels.py, this leaves ai_kb_trader_label.expected EMPTY.
The AI KB reviewer fills the prediction by reading ONLY the bars section, then
Layer 9 reveals divergence vs the deterministic pipeline verdict.
"""
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone

try:
    from validate_layer7_real_chart_cases import load_json, write_json, DEFAULT_CASES_DIR
except ImportError:
    sys.path.insert(0, "knowledge_bot")
    from validate_layer7_real_chart_cases import load_json, write_json, DEFAULT_CASES_DIR

# 12 diverse regimes NOT used in 004-023 (summer chop, alt-rally, ATH/topping)
BLIND_CASES = [
    ("BTCUSDT", "2024-08"),
    ("BTCUSDT", "2025-01"),
    ("ETHUSDT", "2024-09"),
    ("ETHUSDT", "2024-12"),
    ("SOLUSDT", "2024-10"),
    ("SOLUSDT", "2025-02"),
    ("XRPUSDT", "2024-11"),
    ("ADAUSDT", "2024-12"),
    ("DOGEUSDT", "2024-11"),
    ("LINKUSDT", "2024-12"),
    ("AVAXUSDT", "2025-01"),
    ("BNBUSDT", "2024-12"),
]


def build_draft(case_num: int, symbol: str, month: str) -> str:
    case_id = f"L7-USER-REAL-{case_num:03d}"
    cmd = [
        sys.executable,
        "knowledge_bot/build_layer7_real_case_draft.py",
        f"--case-id={case_id}",
        f"--symbol={symbol}",
        f"--start={month}",
        f"--end={month}",
        "--write",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=Path.cwd())
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:300]}", file=sys.stderr)
        return ""
    cases_dir = Path(DEFAULT_CASES_DIR)
    matching = list(cases_dir.glob(f"{case_id}*.template.json"))
    return matching[0].name if matching else ""


def main() -> int:
    cases_dir = Path(DEFAULT_CASES_DIR)
    created = []
    print(f"\n{'='*80}\n[GENERATE BLIND BATCH] {len(BLIND_CASES)} cases (empty labels)\n{'='*80}\n")

    for i, (symbol, month) in enumerate(BLIND_CASES, 1):
        case_num = 23 + i  # 024..035
        print(f"[{i:2d}/{len(BLIND_CASES)}] {symbol} {month} -> L7-USER-REAL-{case_num:03d}", flush=True)
        name = build_draft(case_num, symbol, month)
        if not name:
            print(f"        [FAIL] draft build failed", flush=True)
            continue
        draft_path = cases_dir / name
        case_data = load_json(draft_path)

        # Insert EMPTY-prediction blinded label stub after expectations
        ai_label = {
            "label_origin": "ai_kb_trader_review",
            "confidence": "high",
            "reviewed_by": "ai_kb_reviewer_blind",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "blind_review_notes": f"BLIND stub for {symbol} {month}; prediction filled by reading bars only.",
            "expected": {},
        }
        ordered = {}
        for k, v in case_data.items():
            ordered[k] = v
            if k == "expectations":
                ordered["ai_kb_trader_label"] = ai_label
        if "ai_kb_trader_label" not in ordered:
            ordered["ai_kb_trader_label"] = ai_label
        write_json(draft_path, ordered)
        print(f"        [OK] {name}", flush=True)
        created.append(name)

    print(f"\n{'='*80}\n[SUMMARY] Created {len(created)}/{len(BLIND_CASES)} blind stubs\n{'='*80}\n")
    for n in created:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
