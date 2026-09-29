"""Explicit author exclusions, shared by corpus preparation and training."""
import json
from pathlib import Path


def user_scope(collection: Path) -> tuple[set[int], int]:
    path=Path(collection)/'training/user_scope.json'
    if not path.exists():
        return {85},408
    value=json.loads(path.read_text(encoding='utf-8'))
    skipped=set(value['excluded_scenario_ids'])
    target=value['target_scenarios']
    if (value['source_scenarios']!=409 or any(type(s) is not int or not 1<=s<=409 for s in skipped)
            or target!=409-len(skipped) or len(skipped)!=len(value['excluded_scenario_ids'])):
        raise ValueError('Invalid explicit user corpus scope')
    return skipped,target
