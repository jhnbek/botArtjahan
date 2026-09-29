"""Build causally bounded examples from reviewed arrows and matched Bybit OHLC.

Every target pair is accounted for; unresolved timing is never converted to a
positive training label merely to increase coverage. The source screenshots and
the earlier pixel-based models remain unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .align_scenario_market import COLLECTION, digest, read_jsonl, short_daily_candidate
from .prepare_scenario_training import load_reviews, daily_alignment
from .scenario_bybit_history import _hash
from .scenario_market_match import time_at_x, price_at_y
from .scenario_model import features_for_entry
from .scenario_prerequisites import prerequisite_features_from_ohlc
from .scenario_training_rules import scenario_training_policy
from .scenario_corpus_scope import user_scope
from .scenario_author_entry import resolve_author_entry

DAY=86400000
HOUR=3600000


def closed_prefix(bars: list[dict], cutoff: int, interval: int, *, limit=80) -> list[dict]:
    """Filter before validation and feature extraction; never include partial OHLC."""
    prefix=[b for b in bars if b['close_time_ms']<=cutoff][-limit:]
    if len(prefix)<16:
        raise ValueError('insufficient_causal_market_history')
    if any(b['close_time_ms']!=b['open_time_ms']+interval for b in prefix):
        raise ValueError('invalid_market_close_boundary')
    if any(b['open_time_ms']!=a['close_time_ms'] for a,b in zip(prefix,prefix[1:])):
        raise ValueError('market_history_gap_before_entry')
    if prefix[-1]['close_time_ms']!=cutoff:
        raise ValueError('missing_exact_market_decision_boundary')
    return prefix


def load_matched_history(collection: Path, row: dict, timeframe: str) -> list[dict]:
    image=collection/f'images/{timeframe}_{row["scenario_id"]}.jpg'
    if digest(image)!=row['fingerprint']['source_sha256']:
        raise ValueError('matched_image_changed')
    path=collection/row['cache_file']
    if digest(path)!=row['cache_file_sha256']:
        raise ValueError('matched_market_history_changed')
    payload=json.loads(path.read_text(encoding='utf-8'))
    checksum=payload.pop('cache_sha256')
    if _hash(payload)!=checksum:
        raise ValueError('invalid_market_cache_checksum')
    if payload['identity']['symbol']!=row['symbol'] or payload['identity']['category']!=row['category']:
        raise ValueError('market_identity_mismatch')
    return [b for b in payload['bars'] if b['close_time_ms']<=payload['exchange_snapshot_ms']]


def choose_level(match, review, last_close):
    levels=match['fingerprint']['levels_y']
    if not levels:
        raise ValueError('no_existing_blue_level')
    override=review.get('reference_level_y')
    if override is not None:
        y=min(levels,key=lambda y:abs(y-override))
        if abs(y-override)>3:
            raise ValueError('reviewed_blue_level_not_found')
        method='reviewed_existing_blue_level'
    else:
        if len(levels)!=1:
            raise ValueError('multiple_blue_levels_require_working_level_review')
        y=levels[0]
        method='single_existing_blue_level'
    return price_at_y(match,y), y, method


def user_daily_close_case(collection, annotation, daily, hourly, daily_extraction, group_splits,
                          *, override=None, daily_review=None, hourly_review=None, timing_review=None,
                          daily_only_eligible=True, daily_level_review=None):
    """Resolve an author correction against matched bars, keeping features causal.

    A separately authorized mismatched H1 picture is ignored for scenario 251.
    """
    sid=annotation['scenario_id']
    override=override or dict(timing='immediately_after_signal_daily_close',ignore_mismatched_H1_image=True)
    daily_review=daily_review or {}
    ignore_hourly_image=override.get('ignore_mismatched_H1_image') is True
    if not daily.get('accepted'):
        raise ValueError('author_override_requires_confirmed_D1_market')
    d_bars=load_matched_history(collection,daily,'1D')
    h_bars=load_matched_history(collection,hourly,'1H')
    if daily['symbol']!=hourly['symbol'] or daily['category']!=hourly['category']:
        raise ValueError('author_override_market_identity_mismatch')
    expected_daily_hash=annotation['image_sha256'][f'images/1D_{sid}.jpg']
    if daily_extraction.get('source_sha256',daily_extraction.get('sha256'))!=expected_daily_hash:
        raise ValueError('original_daily_extraction_hash_mismatch')
    if not ignore_hourly_image and not hourly.get('accepted'):
        raise ValueError('author_override_requires_confirmed_H1_market')
    if daily_review and daily_review.get('source_sha256')!=expected_daily_hash:
        raise ValueError('author_override_daily_review_hash_mismatch')
    anchor=daily_review.get('closed_signal_bar_x')
    context_review_used=False
    if anchor is None and (daily_level_review or {}).get('closed_daily_anchor_x') is not None:
        if daily_level_review.get('source_images_sha256')!=annotation['image_sha256']:
            raise ValueError('author_override_daily_context_source_mismatch')
        anchor=daily_level_review['closed_daily_anchor_x']
        context_review_used=True
    if anchor is None:
        anchor=daily_review.get('annotated_event_bar_x',daily_extraction.get('signal_x'))
    if anchor is None: raise ValueError('author_override_daily_anchor_pending')
    signal=time_at_x(daily['fingerprint'],daily,anchor)
    daily_close=signal+DAY
    if context_review_used and daily_level_review.get('observation_close_time_ms')!=daily_close:
        raise ValueError('author_override_daily_context_time_mismatch')
    event_anchor=daily_review.get('annotated_event_bar_x',daily_extraction.get('signal_x'))
    event_signal=signal if event_anchor is None or event_anchor==anchor else time_at_x(daily['fingerprint'],daily,event_anchor)
    if timing_review:
        if (timing_review['required_daily_close_time_ms']!=daily_close
                or timing_review['required_signal_d1_open_time_ms']!=signal):
            raise ValueError('author_override_confirmed_D1_boundary_mismatch')
        if any(timing_review['source_images_sha256'][tf]!=annotation['image_sha256'][f'images/{tf}_{sid}.jpg']
               for tf in ('1D','1H')):
            raise ValueError('author_override_timing_source_mismatch')
    initial_daily=closed_prefix(d_bars,daily_close,DAY)
    if ignore_hourly_image:
        level,y,method=choose_level(daily,{},initial_daily[-1]['close'])
        method='daily_'+method
        visible_end=daily_close+HOUR
    else:
        if (not (hourly_review or {}).get('reference_level_y') and
                len(hourly['fingerprint']['levels_y'])>1 and daily_level_review):
            if daily_level_review.get('source_images_sha256')!=annotation['image_sha256']:
                raise ValueError('author_override_level_review_source_mismatch')
            reviewed_level,_,_=choose_level(daily,daily_level_review,initial_daily[-1]['close'])
            scale=sum(b['high']-b['low'] for b in initial_daily[-15:-1])/14
            candidates=[z for z in hourly['fingerprint']['levels_y']
                        if abs(price_at_y(hourly,z)-reviewed_level)<=.2*scale]
            if len(candidates)!=1:
                raise ValueError('author_override_hourly_level_not_unique')
            hourly_review={**(hourly_review or {}),'reference_level_y':candidates[0]}
        level,y,method=choose_level(hourly,hourly_review or {},initial_daily[-1]['close'])
        daily_levels=[price_at_y(daily,z) for z in daily['fingerprint']['levels_y']]
        scale=sum(b['high']-b['low'] for b in initial_daily[-15:-1])/14
        if not daily_levels or min(abs(p-level) for p in daily_levels)>.2*scale:
            raise ValueError('author_override_working_levels_disagree')
        visible_end=(hourly['first_open_time_ms']+
                     (max(b['slot'] for b in hourly['fingerprint']['bars'])-hourly['first_slot']+1)*HOUR)
    timing=resolve_author_entry(override,h_bars,daily_close,level,annotation['direction'],visible_end)
    decision=timing['decision_time_ms']
    latest_daily_close=decision//DAY*DAY
    d_prefix=closed_prefix(d_bars,latest_daily_close,DAY)
    h_prefix=closed_prefix(h_bars,decision,HOUR)
    group=daily['symbol'].removesuffix('USDT')
    direction=annotation['direction']
    for tf,match in [('1D',daily),('1H',hourly)]:
        if annotation['image_sha256'][f'images/{tf}_{sid}.jpg']!=match['fingerprint']['source_sha256']:
            raise ValueError('author_override_source_image_changed')
    return dict(scenario_id=sid,group=group,split=group_splits.get(group,'train'),direction=direction,
                market_symbol=daily['symbol'],market_category=daily['category'],
                level=level,level_y=y,level_method=method,
                direction_features=prerequisite_features_from_ohlc(d_prefix,level),
                states=[features_for_entry(h_prefix,level,direction)],offsets=[0],
                state_close_times_ms=[decision],**timing,
                annotated_daily_event_open_time_ms=event_signal,signal_d1_open_time_ms=signal,
                latest_closed_d1_time_ms=latest_daily_close,authored_daily_close_marker_time_ms=None,
                marker_offset_from_UTC_daily_close_hours=None,
                original_H1_image_excluded_from_event_mapping=ignore_hourly_image,
                reference_entry_price=h_prefix[-1]['close'],
                source_images_sha256=annotation['image_sha256'],source_review_file='training/user_scope.json',
                source_hourly_cache=hourly['cache_file'],source_daily_cache=daily['cache_file'],
                source_hourly_cache_sha256=hourly['cache_file_sha256'],source_daily_cache_sha256=daily['cache_file_sha256'],
                source_daily_anchor='author_corrected_post_daily_close_entry',
                earlier_states_are_failed_trades=False,direction_daily_only_eligible=daily_only_eligible)


def build_dataset(collection=COLLECTION):
    skipped,target=user_scope(collection)
    user_contract=json.loads((collection/'training/user_scope.json').read_text(encoding='utf-8'))
    daily={r['scenario_id']:r for r in read_jsonl(collection/'training/market_daily_alignment.jsonl')}
    hourly={r['scenario_id']:r for r in read_jsonl(collection/'training/market_hourly_alignment.jsonl')}
    annotations=read_jsonl(collection/'visual_analysis/scenario_analysis.jsonl')
    direction_review_path=collection/'training/market_direction_reviews.jsonl'
    direction_reviews={r['scenario_id']:r for r in read_jsonl(direction_review_path)} if direction_review_path.exists() else {}
    semantics_path=collection/'training/market_direction_pending_reviews.jsonl'
    direction_semantics={r['scenario_id']:r for r in read_jsonl(semantics_path)} if semantics_path.exists() else {}
    timing_path=collection/'training/market_timing_questions.jsonl'
    timing_reviews={r['scenario_id']:r for r in read_jsonl(timing_path)} if timing_path.exists() else {}
    reviews,review_hashes=load_reviews(collection)
    daily_reviews={r['scenario_id']:r for r in read_jsonl(collection/'training/market_daily_anchor_reviews.jsonl')}
    role_files=sorted((collection/'training').glob('market_daily_role_reviews_*.jsonl'))
    for path in role_files:
        for row in read_jsonl(path):
            daily_reviews[row['scenario_id']]={**daily_reviews.get(row['scenario_id'],{}),**row}
    original_audit=json.loads((collection/'training/image_extraction_audit.json').read_text(encoding='utf-8'))
    old_daily={r['scenario_id']:r for r in original_audit['records'] if r['timeframe']=='1D'}
    # Keep all previously assigned instrument groups fixed, including already
    # observed test groups. New ticker identities never reshuffle those groups.
    old_ledger=read_jsonl(collection/'training/direction_training_ledger.jsonl')
    group_splits={str(r['instrument']).upper():r['split'] for r in old_ledger if r.get('split')}
    cases,ledger=[],[]
    for annotation in annotations:
        sid=annotation['scenario_id']
        if sid in skipped: continue
        entry=dict(scenario_id=sid,status='pending',reason=None,author_scenario_valid=True)
        ledger.append(entry)
        try:
            if sid in user_contract.get('entry_time_requires_user_confirmation_ids',[]):
                raise ValueError('awaiting_author_TVX_after_actual_D1_close')
            dm,hm=daily.get(sid),hourly.get(sid)
            override=user_contract.get('entry_overrides',{}).get(str(sid))
            if override:
                case=user_daily_close_case(collection,annotation,dm,hm,old_daily[sid],group_splits,
                     override=override,daily_review=daily_reviews.get(sid),hourly_review=reviews.get(sid),
                     timing_review=timing_reviews.get(sid),
                     daily_level_review=direction_semantics.get(sid),
                     daily_only_eligible=direction_semantics.get(sid,{}).get('daily_only_direction_verified',True))
                cases.append(case)
                entry.update(status='eligible',reason='explicit_author_corrected_D1_close_entry',
                             split=case['split'],group=case['group'],decision_time_ms=case['decision_time_ms'],
                             states=1,entry_resolution=case['entry_resolution'])
                continue
            if (dm and not dm.get('accepted') and hm and hm.get('accepted')
                    and hm.get('independent_hourly_confirmation_for_short_daily') is True):
                dm=short_daily_candidate(dm)
                if dm is not None:
                    dm=dict(dm,accepted=True,acceptance_source='independent_D1_and_H1_geometry')
            if not dm or not dm.get('accepted'): raise ValueError('daily_market_match_pending')
            if not hm or not hm.get('accepted'): raise ValueError('hourly_market_match_pending')
            if (dm['symbol'],dm['category'])!=(hm['symbol'],hm['category']):
                raise ValueError('different_market_between_timeframes')
            review=reviews.get(sid)
            if not review or review.get('reviewed') is not True:
                raise ValueError('entry_arrow_review_pending')
            market_can_supply_daily_boundary=(review.get('explicit_entry_anchor_verified') is True
                                               and review.get('missing_daily_close_only') is True)
            if review.get('usable_for_entry_training') is not True and not market_can_supply_daily_boundary:
                raise ValueError('entry_semantics_unresolved_in_review')
            policy=scenario_training_policy(annotation)
            direction_review=direction_reviews.get(sid,{})
            user_direction=user_contract.get('direction_overrides',{}).get(str(sid))
            if direction_review:
                if direction_review.get('source_images_sha256')!=annotation['image_sha256']:
                    raise ValueError('direction_review_image_hash_mismatch')
                reviewed_path=collection/direction_review['source_entry_review_file']
                if digest(reviewed_path)!=direction_review['source_entry_review_file_sha256']:
                    raise ValueError('direction_review_entry_source_changed')
                if direction_review.get('requires_author_direction_clarification') and not user_direction:
                    raise ValueError('direction_label_awaits_author_clarification')
            if user_direction:
                if user_direction not in ('long','short'):
                    raise ValueError('invalid_author_direction_override')
                direction_review={**direction_review,'entry_direction':user_direction,
                                  'entry_direction_verified':True,'entry_policy_review_resolved':True,
                                  'daily_only_direction_eligible':True,'label_source':'explicit_user_correction'}
            direction_resolved=(direction_review.get('entry_direction_verified') is True
                                and direction_review.get('entry_policy_review_resolved') is True)
            if not policy['entry_ranking_eligible'] and not direction_resolved:
                raise ValueError('direction_or_entry_interpretation_pending')
            if review.get('decision_timing') not in ('after_bar_close','before_bar_open'):
                raise ValueError('entry_decision_timing_unresolved')
            hourly_bars=load_matched_history(collection,hm,'1H')
            daily_bars=load_matched_history(collection,dm,'1D')
            expected_hash=annotation['image_sha256'][f'images/1H_{sid}.jpg']
            if hm['fingerprint']['source_sha256']!=expected_hash:
                raise ValueError('entry_annotation_image_hash_mismatch')
            if review.get('source_sha256',expected_hash)!=expected_hash:
                raise ValueError('entry_review_image_hash_mismatch')
            marked_open=time_at_x(hm['fingerprint'],hm,review['decision_bar_x'])
            after_close=review['decision_timing']=='after_bar_close'
            decision=marked_open+HOUR if after_close else marked_open
            h_prefix=closed_prefix(hourly_bars,decision,HOUR)
            # The complete daily bar at/before the decision is the latest
            # observable D1 context. Compare it with the author's D1 event.
            d_close=(decision//DAY)*DAY
            d_prefix=closed_prefix(daily_bars,d_close,DAY)
            dr=daily_reviews.get(sid,{})
            if direction_review.get('requires_daily_context_role_override') and direction_resolved:
                dr={**dr,'source_sha256':annotation['image_sha256'][f'images/1D_{sid}.jpg'],
                    'closed_context_verified':True,
                    'closed_context_bar_x':direction_review['closed_context_bar_x_at_entry']}
            if dr.get('requires_user_clarification'):
                raise ValueError('daily_arrow_role_awaits_user_clarification')
            daily_hash=annotation['image_sha256'][f'images/1D_{sid}.jpg']
            if dm['fingerprint']['source_sha256']!=daily_hash:
                raise ValueError('daily_annotation_image_hash_mismatch')
            if dr and dr.get('source_sha256')!=daily_hash:
                raise ValueError('daily_review_image_hash_mismatch')
            original_daily=old_daily.get(sid,{})
            if original_daily.get('source_sha256',original_daily.get('sha256'))!=daily_hash:
                raise ValueError('original_daily_extraction_hash_mismatch')
            anchor=dr.get('closed_signal_bar_x') if dr.get('closed_signal_semantics_verified') else None
            source='reviewed_closed_daily_signal' if anchor is not None else 'automatic_daily_event'
            if dr.get('closed_context_verified') is True:
                anchor=dr['closed_context_bar_x']
                source='reviewed_closed_daily_context_before_execution_day'
            if anchor is None:
                anchor=dr.get('annotated_event_bar_x',old_daily.get(sid,{}).get('signal_x'))
            if anchor is None: raise ValueError('daily_event_anchor_pending')
            signal_open=time_at_x(dm['fingerprint'],dm,anchor)
            if source=='reviewed_closed_daily_signal' and signal_open+DAY>decision:
                raise ValueError('entry_before_confirmed_author_daily_signal')
            # Signals earlier than the latest D1 remain valid; a clearly marked
            # execution-day arrow can point to the currently forming day. Do
            # not use that day's future OHLC as the direction observation.
            if signal_open>decision or decision-(signal_open+DAY)>2*DAY:
                raise ValueError('daily_and_hourly_event_window_disagree')
            if signal_open+DAY>decision and dr.get('annotated_event_role')!='execution_day':
                raise ValueError('daily_event_is_unclosed_requires_semantic_review')
            confirmed_signal_open=min(signal_open,d_close-DAY)
            level,y,level_method=choose_level(hm,review,h_prefix[-1]['close'])
            daily_levels=[price_at_y(dm,z) for z in dm['fingerprint']['levels_y']]
            d_scale=sum(b['high']-b['low'] for b in d_prefix[-15:-1])/14
            if not daily_levels or min(abs(p-level) for p in daily_levels)>.2*d_scale:
                raise ValueError('working_level_differs_between_D1_and_H1')
            authored_daily_x,_=daily_alignment(review)
            authored_daily_close=None
            if authored_daily_x is not None:
                authored_daily_close=time_at_x(hm['fingerprint'],hm,authored_daily_x)+HOUR
            # Only an explicit later condition permits negative wait examples.
            # Immediate and repeat-entry episodes contribute their positive
            # state only, even when their visual close arrow differs from UTC.
            explicit_delayed=(review.get('wait_interval_verified') is True
                              and review.get('no_other_valid_entries_in_wait_interval') is True
                              and authored_daily_x is not None
                              and review['decision_bar_x']>authored_daily_x+hm['fingerprint']['pitch']*.65)
            repeated=review.get('entry_variant')=='repeat_entry' or review.get('earlier_entry_labels_require_review',False)
            start_wait=max(d_close,authored_daily_close or d_close)
            state_times=[decision]
            if explicit_delayed and not repeated:
                state_times=list(range(max(start_wait,decision-6*HOUR),decision+1,HOUR))
            direction=direction_review['entry_direction'] if direction_resolved else annotation['direction']
            states=[features_for_entry(closed_prefix(hourly_bars,t,HOUR),level,direction) for t in state_times]
            group=dm['symbol'].removesuffix('USDT')
            split=group_splits.get(group,'train')
            case=dict(scenario_id=sid,group=group,split=split,direction=direction,
                      market_symbol=dm['symbol'],market_category=dm['category'],
                      level=level,level_y=y,level_method=level_method,
                      direction_features=prerequisite_features_from_ohlc(d_prefix,level),
                      states=states,offsets=[(decision-t)//HOUR for t in state_times],
                      state_close_times_ms=state_times,decision_time_ms=decision,
                      annotated_daily_event_open_time_ms=signal_open,
                      signal_d1_open_time_ms=confirmed_signal_open,latest_closed_d1_time_ms=d_close,
                      authored_daily_close_marker_time_ms=authored_daily_close,
                      marker_offset_from_UTC_daily_close_hours=((authored_daily_close-d_close)/HOUR if authored_daily_close else None),
                      entry_resolution='closed_hourly_bar' if after_close else 'pre_hour_proxy_not_exact_intrabar_entry',
                      entry_label_source=review.get('entry_label_source','reviewed_author_entry_anchor'),
                      author_exact_execution_hour_verified=review.get('author_exact_execution_hour_verified'),
                      earliest_OR_execution_verified=review.get('earliest_OR_execution_verified'),
                      reference_entry_price=h_prefix[-1]['close'],exact_execution_price=None,
                      source_images_sha256=annotation['image_sha256'],source_review_file=review['review_file'],
                      source_hourly_cache=hm['cache_file'],source_daily_cache=dm['cache_file'],
                      source_hourly_cache_sha256=hm['cache_file_sha256'],source_daily_cache_sha256=dm['cache_file_sha256'],
                      source_daily_anchor=source,earlier_states_are_failed_trades=False,
                      direction_daily_only_eligible=(direction_semantics.get(sid,{}).get('daily_only_direction_verified',True) and
                                             (direction_review['daily_only_direction_eligible']
                                             if direction_resolved else policy['daily_only_direction_eligible'])))
            cases.append(case)
            entry.update(status='eligible',reason='reviewed_entry_with_causal_market_OHLC',
                         split=split,group=group,decision_time_ms=decision,
                         states=len(states),entry_resolution=case['entry_resolution'])
        except (ValueError,KeyError,TypeError) as exc:
            entry['reason']=str(exc)
    if len(ledger)!=target or len({r['scenario_id'] for r in ledger})!=target:
        raise ValueError(f'Expected exactly {target} target pairs')
    out=collection/'training/market_v2'
    out.mkdir(exist_ok=True)
    for name,rows in [('cases',cases),('ledger',ledger)]:
        (out/f'{name}.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in rows),encoding='utf-8')
    source_paths=['training/user_scope.json','visual_analysis/scenario_analysis.jsonl','training/market_daily_alignment.jsonl',
                  'training/market_hourly_alignment.jsonl','training/market_daily_anchor_reviews.jsonl',
                  'training/image_extraction_audit.json','training/direction_training_ledger.jsonl']
    source_paths.extend(p.relative_to(collection).as_posix() for p in role_files)
    if user_contract.get('author_entry_corrections_source'):
        source_paths.append(user_contract['author_entry_corrections_source'])
    if user_contract.get('author_atr_entry_corrections_source'):
        source_paths.append(user_contract['author_atr_entry_corrections_source'])
        method_path=collection/'training/h1_atr_method.json'
        method=json.loads(method_path.read_text(encoding='utf-8'))
        for relative,expected in method['source_snapshots_sha256'].items():
            if digest(collection/relative)!=expected:
                raise ValueError('hourly_ATR_method_source_changed')
            source_paths.append(relative)
        source_paths.append(method_path.relative_to(collection).as_posix())
    if user_contract.get('atr_condition_review_source'):
        source_paths.append(user_contract['atr_condition_review_source'])
    if user_contract.get('author_final_entry_corrections_source'):
        source_paths.append(user_contract['author_final_entry_corrections_source'])
        method_path=collection/'training/chop_end_method.json'
        method=json.loads(method_path.read_text(encoding='utf-8'))
        for relative,expected in method['source_snapshots_sha256'].items():
            if digest(collection/relative)!=expected:
                raise ValueError('chop_end_method_source_changed')
            source_paths.append(relative)
        source_paths.append(method_path.relative_to(collection).as_posix())
    if direction_review_path.exists():
        source_paths.append(direction_review_path.relative_to(collection).as_posix())
    if semantics_path.exists():
        source_paths.append(semantics_path.relative_to(collection).as_posix())
    timing_path=collection/'training/market_timing_questions.jsonl'
    if timing_path.exists():
        source_paths.append(timing_path.relative_to(collection).as_posix())
    report=dict(target_pairs=target,user_skipped=sorted(skipped),eligible_pairs=len(cases),
                pending_pairs=target-len(cases),all_target_pairs_ready=len(cases)==target,
                reason_counts=dict(Counter(r['reason'] for r in ledger)),
                resolutions=dict(Counter(r['entry_resolution'] for r in cases)),
                split_counts=dict(Counter(r['split'] for r in cases)),
                source_sha256={p:digest(collection/p) for p in source_paths},
                review_sources_sha256=review_hashes,preparation_sha256=digest(Path(__file__)),
                feature_code_sha256={name:digest(Path(__file__).with_name(name)) for name in
                                     ('scenario_prerequisites.py','scenario_image_features.py',
                                      'scenario_model.py','scenario_training_rules.py','scenario_author_entry.py',
                                      'scenario_hourly_atr.py')},
                cases_sha256=digest(out/'cases.jsonl'),automatic_order_execution_allowed=False,
                historical_test_was_previously_observed=True,profitability_evaluated=False)
    (out/'preparation_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection',type=Path,default=COLLECTION)
    args=parser.parse_args()
    print(json.dumps(build_dataset(args.collection),indent=2))
