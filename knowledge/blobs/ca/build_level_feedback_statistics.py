"""Build auditable level-detection feedback statistics.

Human level reviews are training labels, not automatic rule changes. This script
keeps the raw JSONL audit trail intact and creates a derived calibration profile
that can be reviewed before changing discovery thresholds.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from runtime_root import data_root

ROOT = data_root(__file__)
DEFAULT_INPUT = ROOT / "_knowledge_base" / "user_level_feedback.jsonl"
DEFAULT_OUTPUT = ROOT / "_knowledge_base" / "level_feedback_statistics.json"
DEFAULT_REPORT = ROOT / "_knowledge_base" / "level_feedback_statistics.md"


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _key(record: dict[str, Any]) -> tuple[str, str, str, float]:
    return (
        str(record.get("exchange") or "unknown"),
        str(record.get("symbol") or "unknown").upper(),
        str(record.get("interval") or "unknown"),
        float(record.get("price")),
    )


def _reason_codes(record: dict[str, Any]) -> list[str]:
    values = record.get("reason_codes")
    if isinstance(values, list):
        return [str(value) for value in values if value]
    value = record.get("reason_code")
    return [str(value)] if value else []


def _active_labels(records: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, str, float], dict[str, Any]], set[tuple[str, str, str, float]]]:
    active: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    rejected: set[tuple[str, str, str, float]] = set()
    for record in records:
        action = record.get("action")
        if action not in {"hide_robot_level", "restore_robot_level", "add_manual_level", "clear_manual_levels"}:
            continue
        if action == "clear_manual_levels":
            prefix = _key(record)[:3]
            for key in [item for item in active if item[:3] == prefix and active[item].get("label") == "human_accepted"]:
                active.pop(key, None)
            continue
        try:
            key = _key(record)
        except (KeyError, TypeError, ValueError):
            continue
        if action == "hide_robot_level":
            active[key] = {**record, "label": "human_rejected", "training_signal": "negative"}
            rejected.add(key)
        elif action == "restore_robot_level":
            active.pop(key, None)
            rejected.discard(key)
        elif action == "add_manual_level":
            active[key] = {**record, "label": "human_accepted", "training_signal": "positive"}
    return active, rejected


def build_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    active, rejected_keys = _active_labels(records)
    examples = list(active.values())
    negative = [row for row in examples if row.get("label") == "human_rejected"]
    positive = [row for row in examples if row.get("label") == "human_accepted"]

    rejection_reasons = Counter(reason for row in negative for reason in _reason_codes(row))
    acceptance_reasons = Counter(reason for row in positive for reason in _reason_codes(row))
    rejected_status = Counter(str(row.get("kb_status") or "unknown") for row in negative)
    rejected_score = Counter(str(row.get("kb_score") or "unknown") for row in negative)
    by_context: dict[str, dict[str, int]] = defaultdict(lambda: {"accepted": 0, "rejected": 0})
    for row in positive:
        context = ":".join(str(row.get(key) or "unknown") for key in ("exchange", "symbol", "interval"))
        by_context[context]["accepted"] += 1
    for row in negative:
        context = ":".join(str(row.get(key) or "unknown") for key in ("exchange", "symbol", "interval"))
        by_context[context]["rejected"] += 1

    return {
        "schema": "level_feedback_statistics_v1",
        "raw_record_count": len(records),
        "active_example_count": len(examples),
        "active_positive_count": len(positive),
        "active_negative_count": len(negative),
        "restored_or_removed_count": max(0, len(rejected_keys) - len(negative)),
        "reason_counts": {
            "rejected": dict(rejection_reasons.most_common()),
            "accepted": dict(acceptance_reasons.most_common()),
        },
        "rejected_robot_status_counts": dict(rejected_status.most_common()),
        "rejected_robot_score_counts": dict(rejected_score.most_common()),
        "by_context": dict(sorted(by_context.items())),
        "calibration_examples": [
            {
                "label": row.get("label"),
                "training_signal": row.get("training_signal"),
                "exchange": row.get("exchange"),
                "symbol": row.get("symbol"),
                "interval": row.get("interval"),
                "price": row.get("price"),
                "side": row.get("side"),
                "kb_status": row.get("kb_status"),
                "kb_score": row.get("kb_score"),
                "reason_codes": _reason_codes(row),
                "note": row.get("note", ""),
                "recorded_at": row.get("recorded_at"),
            }
            for row in examples
        ],
        "policy": {
            "automatic_rule_update": False,
            "recommended_minimum_examples_before_threshold_change": 30,
            "purpose": "review human labels before changing level discovery rules",
        },
    }


def render_report(statistics: dict[str, Any]) -> str:
    lines = [
        "# Level Feedback Statistics",
        "",
        "Сырые записи не изменяются. Отрицательные примеры означают, что пользователь удалил уровень робота; положительные — что пользователь добавил ручной уровень.",
        "",
        f"- Активных примеров: {statistics['active_example_count']}",
        f"- Положительных ручных уровней: {statistics['active_positive_count']}",
        f"- Отрицательных роботских уровней: {statistics['active_negative_count']}",
        f"- Всего сырых записей: {statistics['raw_record_count']}",
        "",
        "## Причины удаления",
        "",
    ]
    for reason, count in statistics["reason_counts"]["rejected"].items():
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Причины добавления", ""])
    for reason, count in statistics["reason_counts"]["accepted"].items():
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Контексты", ""])
    for context, counts in statistics["by_context"].items():
        lines.append(f"- `{context}`: принято={counts['accepted']}, отклонено={counts['rejected']}")
    lines.extend([
        "",
        "## Правило использования",
        "",
        "Статистика используется как калибровочный набор. Пороговые значения алгоритма не меняются автоматически по одной записи.",
        "",
    ])
    return "\n".join(lines)


def build(input_path: Path = DEFAULT_INPUT, output_path: Path = DEFAULT_OUTPUT,
          report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    statistics = build_statistics(read_records(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(statistics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(statistics), encoding="utf-8")
    return statistics


def main() -> int:
    parser = argparse.ArgumentParser(description="Build level detection feedback statistics.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    statistics = build(args.input, args.output, args.report)
    print(json.dumps({
        "active_examples": statistics["active_example_count"],
        "positive": statistics["active_positive_count"],
        "negative": statistics["active_negative_count"],
        "output": str(args.output),
        "report": str(args.report),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
