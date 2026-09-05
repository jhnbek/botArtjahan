#!/usr/bin/env python3
"""
Batch KB-trader blinded audit:
1. Generate 20 real-case drafts (symbol × month combos)
2. For each, insert ai_kb_trader_label stub
3. Update DRAFT_ALIGNMENT_AUDIT_FILES in Layer 9 validator
4. User will fill in blinded predictions
5. Run Layer 9 to see divergences
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime

try:
    from validate_layer7_real_chart_cases import (
        load_json, write_json, DEFAULT_CASES_DIR, rel_path
    )
except ImportError:
    sys.path.insert(0, "knowledge_bot")
    from validate_layer7_real_chart_cases import (
        load_json, write_json, DEFAULT_CASES_DIR, rel_path
    )

# 20 diverse cases: pairs × month (YYYY-MM format)
AUDIT_CASES = [
    ("BTCUSDT", "2024-02", "2024-02"),
    ("BTCUSDT", "2024-03", "2024-03"),
    ("BTCUSDT", "2024-04", "2024-04"),
    ("BTCUSDT", "2024-05", "2024-05"),
    ("BTCUSDT", "2024-06", "2024-06"),
    ("ETHUSDT", "2024-01", "2024-01"),
    ("ETHUSDT", "2024-02", "2024-02"),
    ("ETHUSDT", "2024-03", "2024-03"),
    ("ETHUSDT", "2024-04", "2024-04"),
    ("SOLUSDT", "2024-01", "2024-01"),
    ("SOLUSDT", "2024-02", "2024-02"),
    ("SOLUSDT", "2024-03", "2024-03"),
    ("ADAUSDT", "2024-01", "2024-01"),
    ("ADAUSDT", "2024-02", "2024-02"),
    ("XRPUSDT", "2024-01", "2024-01"),
    ("XRPUSDT", "2024-02", "2024-02"),
    ("DOGEUSDT", "2024-01", "2024-01"),
    ("BNBUSDT", "2024-01", "2024-01"),
    ("LINKUSDT", "2024-01", "2024-01"),
    ("AVAXUSDT", "2024-01", "2024-01"),
]

def build_case_draft(case_num: int, symbol: str, start: str, end: str) -> tuple[bool, str]:
    """Build draft via build_layer7_real_case_draft.py with unique case_id. Return (ok, draft_filename)."""
    case_id = f"L7-USER-REAL-{case_num:03d}"
    cmd = [
        sys.executable,
        "knowledge_bot/build_layer7_real_case_draft.py",
        f"--case-id={case_id}",
        f"--symbol={symbol}",
        f"--start={start}",
        f"--end={end}",
        "--write",  # Write the template file
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=Path.cwd(),
        )
        if result.returncode == 0:
            # Extract output_file from JSON output
            try:
                for line in result.stdout.split('\n'):
                    if '{' in line:
                        # Found JSON, parse it
                        json_start = result.stdout.find('{')
                        json_text = result.stdout[json_start:]
                        data = json.loads(json_text)
                        if "output_file" in data and data["output_file"]:
                            return True, Path(data["output_file"]).name
            except:
                pass
            # Fallback: find the most recently created .template.json
            cases_dir = Path(DEFAULT_CASES_DIR)
            matching = list(cases_dir.glob(f'L7-USER-REAL-{case_num:03d}*.template.json'))
            if matching:
                return True, matching[0].name
        print(f"  ERROR: {result.stderr[:200]}", file=sys.stderr)
        return False, ""
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False, ""

def main():
    cases_dir = Path(DEFAULT_CASES_DIR)
    created_drafts = []

    print(f"\n{'='*80}")
    print(f"[BATCH KB-TRADER AUDIT] {len(AUDIT_CASES)} cases")
    print(f"{'='*80}\n")

    for i, (symbol, start, end) in enumerate(AUDIT_CASES, 1):
        month_str = start[:7]  # YYYY-MM
        print(f"[{i:2d}/{len(AUDIT_CASES)}] {symbol} {month_str}", flush=True)

        # Build draft with unique case_id (004 onwards, since 001-003 are existing)
        case_num = 3 + i
        ok, draft_name = build_case_draft(case_num, symbol, start, end)
        if not ok or not draft_name:
            print(f"        SKIP: draft build failed", flush=True)
            continue

        draft_path = cases_dir / draft_name
        if not draft_path.exists():
            print(f"        SKIP: draft not found at {draft_path}", flush=True)
            continue

        # Load draft
        try:
            case_data = load_json(draft_path)
        except Exception as e:
            print(f"        SKIP: JSON load failed: {e}", flush=True)
            continue

        # Skip if already has ai_kb_trader_label
        if "ai_kb_trader_label" in case_data:
            print(f"        Already labeled, skip", flush=True)
            created_drafts.append(draft_name)
            continue

        # Require expectations (written by build_layer7_real_case_draft.py)
        if "expectations" not in case_data:
            print(f"        SKIP: no expectations", flush=True)
            continue

        # Create ai_kb_trader_label stub
        ai_label = {
            "label_origin": "ai_kb_trader_review",
            "confidence": "high",
            "reviewed_by": "batch_system",
            "reviewed_at": datetime.utcnow().isoformat() + "Z",
            "blind_review_notes": f"Batch stub for {symbol} {month_str}",
            "expected": {},  # Filled by blinded KB review (do NOT use bot expectations yet)
        }

        # Insert after expectations
        ordered = {}
        for k, v in case_data.items():
            ordered[k] = v
            if k == "expectations":
                ordered["ai_kb_trader_label"] = ai_label

        write_json(draft_path, ordered)
        print(f"        {draft_name}", flush=True)
        created_drafts.append(draft_name)

    print(f"\n{'='*80}")
    print(f"[SUMMARY] Created {len(created_drafts)}/{len(AUDIT_CASES)} stubs")
    print(f"{'='*80}\n")

    if created_drafts:
        # Update DRAFT_ALIGNMENT_AUDIT_FILES in validate_layer9_trader_alignment.py
        validator_path = Path("knowledge_bot/validate_layer9_trader_alignment.py")
        if validator_path.exists():
            try:
                content = validator_path.read_text(encoding='utf-8')

                # Build new tuple string
                tuple_lines = ["DRAFT_ALIGNMENT_AUDIT_FILES = ("]
                for name in created_drafts:
                    tuple_lines.append(f'    "{name}",')
                tuple_lines.append(")")
                new_tuple_str = '\n'.join(tuple_lines)

                # Find and replace the old tuple
                pattern = r'DRAFT_ALIGNMENT_AUDIT_FILES = \([^)]*\)'
                new_content = re.sub(pattern, new_tuple_str, content, count=1, flags=re.DOTALL)

                validator_path.write_text(new_content, encoding='utf-8')
                print(f"✓ Updated validate_layer9_trader_alignment.py with {len(created_drafts)} drafts\n")
            except Exception as e:
                print(f"✗ Failed to update validator: {e}\n", file=sys.stderr)

    print("[NEXT STEPS]")
    print(f"1. Review each of the {len(created_drafts)} .template.json draft files")
    print("2. For each, read ONLY the bars section (do NOT read expectations)")
    print("3. Fill in ai_kb_trader_label.expected with your blinded KB prediction")
    print("4. Run: python knowledge_bot/validate_layer9_trader_alignment.py --strict-exit-code")
    print("5. Analyze draft_alignment_divergences\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
