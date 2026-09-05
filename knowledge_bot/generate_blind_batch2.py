#!/usr/bin/env python3
"""
Generate the SECOND blinded batch of 12 real-case drafts (L7-USER-REAL-036..047).
Same mechanism as generate_blind_batch.py but a fresh START_CASE and new regimes
(bear/crash months added to stress-test the engine's fade-extreme edge).
Leaves ai_kb_trader_label.expected EMPTY for true blinded review.
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

START_CASE = 36
# 12 NEW regimes not used in 004-035: bear/crash + fresh topping/recovery windows
BLIND_CASES = [
    ("BTCUSDT", "2022-06"),  # bear capitulation
    ("BTCUSDT", "2025-02"),  # post-ATH topping
    ("ETHUSDT", "2022-05"),  # LUNA-collapse month
    ("ETHUSDT", "2025-02"),
    ("SOLUSDT", "2022-11"),  # FTX collapse
    ("SOLUSDT", "2025-01"),
    ("XRPUSDT", "2024-12"),  # post-pump cooldown
    ("ADAUSDT", "2025-01"),
    ("DOGEUSDT", "2024-12"),
    ("LINKUSDT", "2025-01"),
    ("AVAXUSDT", "2024-12"),
    ("BNBUSDT", "2025-01"),
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
    print(f"\n{'='*80}\n[GENERATE BLIND BATCH 2] {len(BLIND_CASES)} cases (empty labels)\n{'='*80}\n")

    for i, (symbol, month) in enumerate(BLIND_CASES):
        case_num = START_CASE + i  # 036..047
        print(f"[{i+1:2d}/{len(BLIND_CASES)}] {symbol} {month} -> L7-USER-REAL-{case_num:03d}", flush=True)
        name = build_draft(case_num, symbol, month)
        if not name:
            print(f"        [FAIL] draft build failed", flush=True)
            continue
        draft_path = cases_dir / name
        case_data = load_json(draft_path)

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
