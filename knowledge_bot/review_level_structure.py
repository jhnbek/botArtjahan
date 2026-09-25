"""Reproduce an offline comparison; annotations are never detector inputs."""
import argparse
import hashlib
import json
import subprocess
import sys
import types
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from chart_level_modes import closed_candle_rows, rejected_contact_constraints
from build_level_feedback_statistics import read_records
from level_discovery import Bar, DiscoveryParams, discover_levels
from level_structure import StructureParams, level_events
from level_history import daily_level_history


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, help='UTC instant, e.g. 2026-09-23T06:00:00Z')
    parser.add_argument('--baseline', default='90273e98cd02be476239d7b41f810892c1447a8e')
    parser.add_argument('--review-json', type=Path, help='Separate revised references; not detector inputs')
    parser.add_argument('--output-directory', type=Path, help='Keep a new review separate from prior reports')
    parser.add_argument('--bsu-feedback', type=Path, help='Explicit manual BSU corrections for the Bybit BTCUSDT 1d review')
    args = parser.parse_args()
    source = root/'_knowledge_base/manual_reviews/strong_levels_review_20260923'
    out = root/'_knowledge_base/manual_reviews/strong_levels_algorithm_20260923'
    rule_path = out/'rule_clarification.json'
    review_path = args.review_json or source/'review.json'
    out = args.output_directory or out
    review = json.loads(review_path.read_text(encoding='utf-8'))
    snapshot = json.loads((source/'candles.json').read_text(encoding='utf-8'))
    instant = int(datetime.fromisoformat(args.as_of.replace('Z','+00:00')).timestamp()*1000)
    rows = closed_candle_rows(snapshot['bars'],as_of_ms=instant)
    closed_count = len(rows)
    rows, history = daily_level_history(rows, interval='1d', as_of_ms=instant)
    bars = [Bar(int(r['open_time_ms']),*[float(r[k]) for k in ('open','high','low','close','volume')]) for r in rows]
    exclusions = rejected_contact_constraints(read_records(args.bsu_feedback),('bybit','BTCUSDT','1d')) if args.bsu_feedback else ()
    levels = discover_levels(bars,DiscoveryParams(nearest_window_atr=float('inf'),excluded_contacts=exclusions),as_of_ms=instant,interval='1d')
    strong = [lv for lv in levels if lv.structure.get('strong')]
    auto = [lv for lv in levels if lv.structure.get('strong') or 'inflection' in lv.basis_tags]
    # Compare the same closed snapshot with the exact previous implementation.
    git = ['git','-c',f'safe.directory={root.as_posix()}','-C',str(root)]
    previous = subprocess.check_output(git+['show',args.baseline+':knowledge_bot/level_discovery.py'],text=True,encoding='utf-8')
    baseline = types.ModuleType('_baseline_level_discovery')
    sys.modules[baseline.__name__] = baseline
    exec(compile(previous,'<baseline_level_discovery>','exec'),baseline.__dict__)
    old = baseline.discover_levels(bars,baseline.DiscoveryParams(nearest_window_atr=float('inf')))
    old_typed = [lv for lv in old if set(lv.basis_tags)&{'mirror_level','limit_level','two_bar_limit'}]
    old_auto = [lv for lv in old if lv in old_typed or 'inflection' in lv.basis_tags]
    comparisons=[]
    for expected in review['levels']:
        price = expected['price_approx']
        nearest = min(auto,key=lambda lv:abs(lv.price-price))
        before = min(old_auto,key=lambda lv:abs(lv.price-price))
        dates=[]
        for touch in expected['touches']:
            events = [e for e in nearest.structure.get('events',[]) if e['time'][:10]==touch['date']]
            dates.append({**touch,'automatic_roles':[e['role'] for e in events]})
        comparisons.append({'id':expected['id'],'expected_price':price,'automatic_price':nearest.price,
            'exact_reference_price':abs(nearest.price-price)<1e-8,
            'exact_price_contact_count':nearest.structure.get('exact_price_contact_count'),
            'price_selection':nearest.structure.get('price_selection',{}),
            'price_error_percent':100*abs(nearest.price/price-1),'price_match_0_1_percent':abs(nearest.price/price-1)<=.001,
            'before_price':before.price,'before_price_match_0_1_percent':abs(before.price/price-1)<=.001,
            'basis_tags':nearest.basis_tags,'bsu_time':nearest.bsu_time,
            'explicit_types_present':set(expected['explicit_types'])<=set(nearest.basis_tags),
            'observations':dates,'currently_chopped':nearest.structure.get('currently_chopped'),
            'false_breakouts_at_reference_price':[
                {'time':e['time'],'role':e['role'],'known_time':e['known_time']}
                for e in level_events(bars,price) if e['role'].startswith('false_breakout')
                and any(e['time'][:10] in fb['dates'] for fb in expected.get('false_breakouts',[]))]})
    channels = {(c['lower'],c['upper'],c['start_time'],c['end_time']):c for lv in strong for c in lv.structure['channels']}
    channel_comparisons=[]
    for expected in review['channels']:
        matches=[c for c in channels.values() if abs(c['lower']/expected['lower']-1)<=.001
                 and abs(c['upper']/expected['upper']-1)<=.001
                 and c['end_time'][:10]>=expected['start'] and c['start_time'][:10]<=expected['end']]
        channel_comparisons.append({**expected,'matching_automatic_channels':matches})
    result={'as_of':args.as_of,'source_bars':len(snapshot['bars']),'closed_bars':closed_count,
        'analyzed_bars':len(bars),'history_window':history,
        'last_closed_bar':rows[-1]['open_time'],'manual_overlays_used':False,
        'manual_bsu_constraints_used':bool(exclusions),'manual_bsu_constraint_count':len(exclusions),
        'baseline_revision':args.baseline,'baseline_scope':'Previous price/type detection, shared current validator; not a comparison of legacy scores',
        'methodology_rule':'False breakouts are entry context only, never level confirmation or strength.',
        'parameters':asdict(StructureParams()),
        'counts':{'before_mirror_limit':len(old_typed),'after_mirror_limit':len(strong),
            'before_price_regions_matched':sum(c['before_price_match_0_1_percent'] for c in comparisons),
            'after_price_regions_matched':sum(c['price_match_0_1_percent'] for c in comparisons),
            'reviewed_price_regions':len(comparisons),'automatic_channels':len(channels),
            'reviewed_channels_matched':sum(bool(c['matching_automatic_channels']) for c in channel_comparisons)},
        'interpretation':'Price-region coverage is not precision, trading performance or validation of every touch/type. Unmentioned levels are unlabelled.',
        'levels':comparisons,'channels':channel_comparisons,
        'automatic_level_summary':[{'price':lv.price,'bsu_time':lv.bsu_time,'basis_tags':lv.basis_tags,
            'contacts':lv.touch_count,'strong_reactions':lv.structure['strong_reaction_count'],
            'currently_chopped':lv.structure['currently_chopped'],'qualified_time':lv.structure['qualified_time']} for lv in strong]}
    files = [review_path.resolve(),source/'candles.json',rule_path] + [Path(__file__).parent/name for name in
        ('level_structure.py','level_discovery.py','level_history.py','detector_prototype.py','chart_level_modes.py','review_level_structure.py')]
    if args.bsu_feedback:
        files.append(args.bsu_feedback.resolve())
    result['sha256']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    out.mkdir(parents=True,exist_ok=True)
    (out/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result['counts'],ensure_ascii=False))


if __name__ == '__main__':
    main()
