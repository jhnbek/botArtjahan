"""Audit rejected BSU associations in computation, without chart hiding state."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from build_level_feedback_statistics import read_records
from chart_level_modes import (active_bsu_rejections, closed_candle_rows,
                               rejected_contact_constraints, evidence_markers)
from level_discovery import Bar, DiscoveryParams, discover_levels
from level_structure import level_events, StructureParams, atr_series


def main():
    root=Path(__file__).resolve().parents[1]
    reviews=root/'_knowledge_base/manual_reviews'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory',type=Path,default=reviews/'bsu_distance_20260924')
    out=parser.parse_args().output_directory
    report_path=out/'comparison.json'
    report=json.loads(report_path.read_text(encoding='utf-8'))
    # Fail rather than audit an outdated comparison against a different method.
    for relative,expected in report['sha256'].items():
        if hashlib.sha256((root/relative).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Rebuild comparison.json first: changed input '+relative)
    instant=int(datetime.fromisoformat(report['as_of'].replace('Z','+00:00')).timestamp()*1000)
    snapshot=json.loads((reviews/'strong_levels_review_20260923/candles.json').read_text(encoding='utf-8'))
    rows=closed_candle_rows(snapshot['bars'],as_of_ms=instant)
    bars=[Bar(int(r['open_time_ms']),*[float(r[k]) for k in ('open','high','low','close','volume')]) for r in rows]
    records=read_records(root/'_knowledge_base/user_level_feedback.jsonl')
    key=('bybit','BTCUSDT','1d')
    rejected=list(active_bsu_rejections(records,key).values())
    constraints=rejected_contact_constraints(records,key)
    params=DiscoveryParams(nearest_window_atr=float('inf'),excluded_contacts=constraints)
    levels=discover_levels(bars,params,as_of_ms=instant,interval='1d')
    candidates=[lv for lv in levels if lv.structure.get('strong') or 'inflection' in lv.basis_tags]
    by_time={b.open_time:i for i,b in enumerate(bars)}
    atrs=atr_series(bars,params.atr_period)
    pairs=[]
    for item in rejected:
        index=by_time[item['bar_open_time_ms']]
        kind=item['marker']['kind']
        price=float(item['price'])
        bar=bars[index]
        wick=bar.high if kind=='H' else bar.low
        # This first check uses ONLY the general rule, no manual constraints.
        events=level_events(bars,price)
        accepted=any(e['index']==index and e['kind']==kind and e['confirms_level'] for e in events)
        nearest=min(candidates,key=lambda lv:abs(lv.price-price))
        markers=evidence_markers(bars,nearest,'mirror_limit',params)
        visible=any(p['index']==index and p['kind']==kind and p['symbol']=='o' for p in markers)
        pairs.append({'level_price':price,'bar_time':item['marker']['time'],
                      'kind':kind,'wick_price':wick,'gap':abs(wick-price),
                      'gap_atr':abs(wick-price)/atrs[index],
                      'gap_percent':abs(wick-price)/price*100,
                      'before_role':item['marker']['evidence'][0]['role'],
                      'general_rule_still_confirms':accepted,
                      'new_nearest_level':nearest.price,
                      'reappears_after_rebuild':visible})
    baseline=json.loads((reviews/'levels_rebuild_20260924/deviation_summary.json').read_text(encoding='utf-8'))
    errors=[abs(v['automatic_price']-v['expected_price'])/abs(v['expected_price'])*100 for v in report['levels']]
    result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'as_of':report['as_of'],
            'scope':{'exchange':key[0],'symbol':key[1],'interval':key[2]},
            'rejected_associations':len(pairs),
            'excluded_by_general_rule':sum(not p['general_rule_still_confirms'] for p in pairs),
            'absent_after_rebuild':sum(not p['reappears_after_rebuild'] for p in pairs),
            'manual_constraints_used':True,'chart_hidden_marker_state_used':False,
            'method_parameters':StructureParams().__dict__,
            'previous_mean_absolute_deviation_percent':baseline['mean_absolute_deviation_percent'],
            'mean_absolute_deviation_percent':sum(errors)/len(errors),
            'reference_count':len(errors),'matches_within_0_1_percent':sum(v['price_match_0_1_percent'] for v in report['levels']),
            'rows':pairs,'comparison_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest()}
    (out/'rejected_bsu_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','method_parameters')},ensure_ascii=False))
    if any(p['general_rule_still_confirms'] or p['reappears_after_rebuild'] for p in pairs):
        raise SystemExit('Some rejected BSUs still contribute; inspect audit.')


if __name__=='__main__':main()
