"""Audit the full working-level selection, including explicitly rejected output."""
import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from build_level_feedback_statistics import read_records
from chart_level_modes import (closed_candle_rows, rejected_contact_constraints, rejected_level_prices,
                               reviewed_level_rejections, working_rejected_level_prices, active_manual_prices)
from level_discovery import Bar, DiscoveryParams, discover_levels
from level_history import daily_level_history


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--as-of')
    parser.add_argument('--output-directory',type=Path,required=True)
    parser.add_argument('--review',type=Path,default=root/'_knowledge_base/manual_reviews/spacing_and_rejections_20260924/review.json')
    parser.add_argument('--current-manual',action='store_true',help='Compare against current chart lines, not old approximate references')
    parser.add_argument('--match-tolerance-percent',type=float,default=0.1)
    args=parser.parse_args()
    if not 0 <= args.match_tolerance_percent <= 100:
        parser.error('--match-tolerance-percent must be between 0 and 100')
    snapshot=json.loads(args.snapshot.read_text(encoding='utf-8'))
    as_of=args.as_of or snapshot['fetched_at']
    instant=int(datetime.fromisoformat(as_of.replace('Z','+00:00')).timestamp()*1000)
    rows=closed_candle_rows(snapshot['bars'],as_of_ms=instant)
    bars=[Bar(int(r['open_time_ms']),*[float(r[k]) for k in ('open','high','low','close','volume')]) for r in rows]
    _,history=daily_level_history(bars,interval='1d',as_of_ms=instant)
    key=('bybit','BTCUSDT','1d')
    journal=root/'_knowledge_base/user_level_feedback.jsonl'
    records=read_records(journal)
    constraints=rejected_contact_constraints(records,key)
    mode_rejected=rejected_level_prices(records,key,'mirror_limit')
    review_rejected=reviewed_level_rejections(records,key)
    rejected=working_rejected_level_prices(records,key,'mirror_limit')
    base=dict(nearest_window_atr=float('inf'),excluded_contacts=constraints)
    candidates=discover_levels(bars,DiscoveryParams(**base,working_selection=False),interval='1d',as_of_ms=instant)
    general_audit=[]
    general=discover_levels(bars,DiscoveryParams(**base),interval='1d',as_of_ms=instant,selection_audit=general_audit)
    final_audit=[]
    params=DiscoveryParams(**base,excluded_level_prices=rejected)
    final=discover_levels(bars,params,interval='1d',as_of_ms=instant,selection_audit=final_audit)
    def mirror(level):return bool(level.structure.get('strong') and set(level.basis_tags)&{'mirror_level','limit_level','two_bar_limit'})
    displayed=[v for v in final if mirror(v)]
    explicit=json.loads((args.review.parent/'rejected_robot_levels.json').read_text(encoding='utf-8'))['records']
    general_prices={v.price for v in general}
    final_prices={v.price for v in final}
    rejected_prices={float(r['price']) for r in explicit}
    survived=sorted(rejected_prices&general_prices)
    review=json.loads(args.review.read_text(encoding='utf-8'))
    if args.current_manual:
        review={'levels':[{'id':f'M{i:02d}','price_approx':price}
                          for i,price in enumerate(active_manual_prices(records,key),1)]}
    # One-to-one price-region matches; unmatched candidates are reported, not hidden.
    pairs=sorted((abs(lv.price/ref['price_approx']-1),ref['id'],lv.price)
                 for ref in review['levels'] for lv in displayed
                 if abs(lv.price/ref['price_approx']-1)<=args.match_tolerance_percent/100)
    matches={};used=set()
    for error,identifier,price in pairs:
        if identifier not in matches and price not in used:
            matches[identifier]={'automatic_price':price,'error_percent':error*100};used.add(price)
    comparisons=[{'id':ref['id'],'user_price':ref['price_approx'],
                  'status':'matched' if ref['id'] in matches else 'missing_within_tolerance',
                  **matches.get(ref['id'],{})} for ref in review['levels']]
    ordered=sorted(final_prices)
    gaps=[{'lower':lo,'upper':hi,'percent':(hi-lo)/lo*100} for lo,hi in zip(ordered,ordered[1:])]
    assert all(g['percent']>=1.5-1e-9 for g in gaps)
    assert not rejected_prices&final_prices
    assert not set(rejected)&final_prices
    counts={'raw_candidates':len(candidates),'raw_mirror_limit':sum(mirror(v) for v in candidates),
            'general_selected_all_types':len(general),'general_selected_mirror_limit':sum(mirror(v) for v in general),
            'with_manual_rejections_all_types':len(final),'with_manual_rejections_mirror_limit':len(displayed),
            'explicit_58_still_found_by_general_rules':len(survived),
            'explicit_58_still_found_with_manual_constraints':len(rejected_prices&final_prices),
            'reviewed_whole_level_constraints':len(set().union(*review_rejected.values())),
            'reviewed_whole_levels_still_found_by_general_rules':len(set().union(*review_rejected.values())&general_prices),
            'reviewed_whole_levels_still_found_with_manual_constraints':len(set().union(*review_rejected.values())&final_prices),
            'reference_price_matches':len(matches),'reference_count':len(comparisons),
            'selected_without_reference_match':sum(v.price not in used for v in displayed)}
    inputs=[args.snapshot,args.review,journal,Path(__file__),root/'knowledge_bot/level_selection.py',root/'knowledge_bot/level_discovery.py',root/'knowledge_bot/level_structure.py',root/'knowledge_bot/chart_level_modes.py']
    inputs += [root/'knowledge_bot/level_evidence_strength.py',root/'knowledge_bot/level_origin_context.py']
    parameter_report={**asdict(params),'nearest_window_atr':'unlimited'}
    result={'as_of':as_of,'source_snapshot':str(args.snapshot),'closed_bars':len(bars),
            'history_window':history,
            'counts':counts,'parameters':parameter_report,'bsu_constraint_count':len(constraints),
            'robot_level_constraint_count':len(rejected),'chart_hiding_used':False,
            'robot_level_constraint_sources':{'mirror_limit':mode_rejected,**review_rejected},
            'general_rule_surviving_user_rejections':survived,
            'reference_matching':'one_to_one_within_tolerance_unmatched_output_retained',
            'reference_source':'current_manual_chart_lines' if args.current_manual else str(args.review),
            'match_tolerance_percent':args.match_tolerance_percent,
            'reference_comparison':comparisons,'spacing':gaps,
            'general_selected_levels':[asdict(v) for v in general],
            'full_selected_levels':[asdict(v) for v in final],
            'general_audit':general_audit,'final_audit':final_audit,
            'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    out=args.output_directory;out.mkdir(parents=True,exist_ok=True)
    (out/'snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'selection_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Full working-level selection audit','',f"As of {as_of}; closed bars: {len(bars)}; retained within 18 months: {history['retained_count']}.",'',
           '| Metric | Count |','|---|---:|']
    lines += [f'| {name} | {count} |' for name,count in counts.items()]
    lines += ['','## All selected levels','', '| Price | Strength | Mode |','|---:|---:|---|']
    lines += [f"| {v.price:.1f} | {v.selection['strength_score']:.3f} | {', '.join(v.basis_tags)} |" for v in sorted(final,key=lambda lv:lv.price)]
    lines += ['','## Every candidate decision','', '| Price | Decision | Reasons | Stronger neighbour |','|---:|---|---|---:|']
    lines += [f"| {a['price']:.1f} | {a['decision']} | {', '.join(a['reasons'])} | {a.get('stronger_price','')} |" for a in sorted(final_audit,key=lambda a:a['price'])]
    general_by_price={a['price']:a for a in general_audit}
    lines += ['','## Your 58 rejections and the general rule result','',
              'Reasons can overlap. Known rejected prices are then excluded before final selection.', '',
              '| Price | Your reasons | Your comment | General rule |', '|---:|---|---|---|']
    def cell(value):return str(value).replace('|','/').replace('\r',' ').replace('\n',' ')
    for record in sorted(explicit,key=lambda r:r['price']):
        automatic=general_by_price.get(record['price'])
        outcome=(automatic['decision']+': '+', '.join(automatic['reasons'])) if automatic else 'not a candidate on this snapshot'
        reasons=record.get('reason_labels') or record.get('reason_codes',[])
        lines.append(f"| {record['price']:.1f} | {cell('; '.join(reasons))} | {cell(record.get('note',''))} | {cell(outcome)} |")
    (out/'selection_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(counts,ensure_ascii=False))


if __name__=='__main__':main()
