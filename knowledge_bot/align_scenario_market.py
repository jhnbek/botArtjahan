"""Reproducible D1/H1 market alignment for every complete author pair."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .scenario_market_match import chart_fingerprint, match_fingerprint, MATCHER_SOURCE_SHA256
from .scenario_bybit_history import BybitHistoryClient, _hash
from .scenario_corpus_scope import user_scope

COLLECTION = Path(__file__).resolve().parents[1]/'_knowledge_base/manual_reviews/scenarios_dzhahan_20260925'
DAY = 86400000


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def short_daily_candidate(row):
    """A shorter exact sequence needs an independent matching H1 chart."""
    match=row.get('best_candidate') or {}
    if (12<=match.get('matched_stems',0)<16 and match.get('color_agreement',0)==1
            and match.get('median_range_error',1)<=.06 and match.get('p90_range_error',1)<=.12
            and (match.get('quality_margin') is None or match['quality_margin']>=.3)):
        return {**row,**match,'accepted':False,'requires_independent_hourly_confirmation':True}
    return None


def write_report(collection, name, rows):
    folder = collection/'training'
    skipped,target=user_scope(collection)
    (folder/f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    summary = dict(target_pairs=target, recorded=len(rows), user_skipped=sorted(skipped),
                   accepted=sum(r.get('accepted',False) for r in rows),
                   reasons=dict(Counter(r['reason'] for r in rows)),
                   training_eligible=False, scope='historical alignment only; decision anchors separately reviewed',
                   matcher_sha256=MATCHER_SOURCE_SHA256)
    (folder/f'{name}_report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


def run_daily(collection=COLLECTION):
    skipped,_=user_scope(collection)
    metadata=read_jsonl(collection/'training/market_metadata.jsonl')
    histories={}
    for path in sorted((collection/'training/bybit_history').glob('*_1d_*.json')):
        data=json.loads(path.read_text(encoding='utf-8'))
        checksum=data.pop('cache_sha256')
        if _hash(data)!=checksum:
            raise ValueError(f'Market cache checksum mismatch: {path}')
        ident=data['identity']
        if ident['category']=='linear':
            # This offline matcher only uses previously downloaded closed days.
            bars=[b for b in data['bars'] if b['close_time_ms']<=data['exchange_snapshot_ms']]
            histories[ident['symbol']]=(bars,path)
    if not histories:
        raise ValueError('Download daily Bybit histories first')
    rows=[]
    for meta in metadata:
        sid=meta['scenario_id']
        if sid in skipped: continue
        row=dict(scenario_id=sid,accepted=False,reason='no_confirmed_market_alignment',
                 matcher_sha256=MATCHER_SOURCE_SHA256,
                 caption_symbol=meta['bybit_symbol_candidate'])
        rows.append(row)
        try:
            fp=chart_fingerprint(collection/f'images/1D_{sid}.jpg')
        except ValueError as exc:
            row['reason']=str(exc); continue
        row['fingerprint']=fp
        preferred=meta['bybit_symbol_candidate']
        candidates=([preferred]+[s for s in sorted(histories) if s!=preferred]
                    if preferred in histories else sorted(histories))
        date_hints=[int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()*1000)
                    for d in meta['caption_date_candidates']]
        matches=[]
        for symbol in candidates:
            bars,path=histories[symbol]
            # Search the full downloaded history so uniqueness is not merely
            # an artifact of trusting a caption date/year.
            match=match_fingerprint(fp,bars)
            matches.append(dict(match,symbol=symbol,category='linear',
                                cache_file=path.relative_to(collection).as_posix(), cache_file_sha256=digest(path)))
            # A confirmed caption instrument has priority. If it cannot match,
            # search other instruments and preserve the caption discrepancy.
            if symbol==preferred and match['accepted']:
                break
        matches.sort(key=lambda r:r.get('quality',float('inf')))
        accepted=[r for r in matches if r['accepted']]
        if len(accepted)==1:
            row.update(accepted[0])
        elif len(accepted)>1:
            row.update(reason='multiple_instruments_match',candidates=accepted)
        else:
            row.update(reason='no_confirmed_market_alignment',best_candidate=matches[0] if matches else None)
        print(f'D1 #{sid}: {row["reason"]} {row.get("symbol", "")}',flush=True)
    return write_report(collection,'market_daily_alignment',rows)


def run_hourly(collection=COLLECTION,scenario_ids=None):
    skipped,_=user_scope(collection)
    daily=read_jsonl(collection/'training/market_daily_alignment.jsonl')
    client=BybitHistoryClient(cache_dir=collection/'training/bybit_history')
    outpath=collection/'training/market_hourly_alignment.jsonl'
    existing={r['scenario_id']:r for r in read_jsonl(outpath)} if outpath.exists() else {}
    rows=[]
    for daily_row in daily:
        sid=daily_row['scenario_id']
        if sid in skipped: continue
        # Resumable confirmed pairs are bound to both source images and cache.
        old=existing.get(sid)
        if scenario_ids is not None and sid not in scenario_ids:
            if old is None:
                rows.append(dict(scenario_id=sid,accepted=False,reason='not_yet_processed'))
            else:
                rows.append(old)
            continue
        matcher_hash=MATCHER_SOURCE_SHA256
        if (old and old.get('accepted')
                and old.get('daily_alignment_sha256')==digest(collection/'training/market_daily_alignment.jsonl')
                and old.get('fingerprint',{}).get('source_sha256')==digest(collection/f'images/1H_{sid}.jpg')
                and old.get('cache_file') and (collection/old['cache_file']).is_file()
                and old.get('cache_file_sha256')==digest(collection/old['cache_file'])
                and old.get('matcher_sha256')==matcher_hash):
            rows.append(old); continue
        row=dict(scenario_id=sid,accepted=False,reason='daily_market_alignment_pending')
        rows.append(row)
        crosscheck=False
        if not daily_row['accepted']:
            candidate=short_daily_candidate(daily_row)
            if candidate is None: continue
            daily_row=candidate
            crosscheck=True
        try:
            fp=chart_fingerprint(collection/f'images/1H_{sid}.jpg')
            start=daily_row['first_open_time_ms']-2*DAY
            # The whole visible D1 window contains the H1 illustration; no
            # author date is silently substituted for the decision timestamp.
            daily_slots=[b['slot'] for b in daily_row['fingerprint']['bars']]
            end=start+(max(daily_slots)-min(daily_slots)+6)*DAY
            history=client.fetch_range(daily_row['symbol'],'1h',start,end,category=daily_row['category'])
            match=match_fingerprint(fp,history['bars'])
            path=Path(history['cache_path'])
            row.update(match,fingerprint=fp,symbol=daily_row['symbol'],category=daily_row['category'],
                       independent_hourly_confirmation_for_short_daily=crosscheck,
                       cache_file=path.relative_to(collection).as_posix(),cache_file_sha256=digest(path),
                       matcher_sha256=matcher_hash,
                       daily_alignment_sha256=digest(collection/'training/market_daily_alignment.jsonl'))
        except (ValueError,RuntimeError,OSError) as exc:
            row.update(reason='hourly_download_or_extraction_failed',error=str(exc))
        print(f'H1 #{sid}: {row["reason"]}',flush=True)
        # Every successful read survives interruption; no all-or-nothing batch.
        write_report(collection,'market_hourly_alignment',rows)
    return write_report(collection,'market_hourly_alignment',rows)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection',type=Path,default=COLLECTION)
    parser.add_argument('--hourly',action='store_true')
    parser.add_argument('--scenario-ids',type=int,nargs='+')
    args=parser.parse_args()
    result=(run_hourly(args.collection,set(args.scenario_ids) if args.scenario_ids else None)
            if args.hourly else run_daily(args.collection))
    print(json.dumps(result,indent=2))
