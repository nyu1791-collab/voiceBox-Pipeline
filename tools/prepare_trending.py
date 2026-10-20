"""Discover trends, follow approved official hosts, capture evidence, then write once."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import atomic_json, load_project
from research import research, extract_source, source_id, script_prompt, generate_script, normalize_script
from renderer.material_capture import capture_targets


@contextmanager
def measured_stage(timing, name):
    stamp = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    status = 'FAILED'
    try:
        yield
        status = 'COMPLETE'
    finally:
        entry = {'stage': name, 'started_utc': stamp,
                 'finished_utc': datetime.now(timezone.utc).isoformat(),
                 'seconds': round(time.monotonic()-started, 6), 'status': status}
        timing['stages'].append(entry)
        print(json.dumps(entry), flush=True)


def official_url(url, hosts):
    """Exact host matching: a mention of a company in a URL is insufficient."""
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.hostname in hosts)


def select_official(pack, hosts, since, until, maximum=4):
    candidates = {}
    known = {}
    for row in pack['sources']:
        if row.get('extraction_status'):
            known[row['url']] = row
            known[row.get('final_url', row['url'])] = row
        for url in [row.get('final_url', row['url']), *row.get('links', [])]:
            if official_url(url, hosts):
                candidates.setdefault(url, {'id': source_id(url), 'url': url, 'title': '',
                                           'discovered_from': row['url']})
    # Bound requests, preserve provenance, and never use aggregator text as primary evidence.
    def resolve(candidate):
        cached = known.get(candidate['url'])
        # Reuse fetched bodies and failures; a discovery failure is not silently retried.
        return {**candidate, **cached} if cached is not None else extract_source(candidate)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(resolve, list(candidates.values())[:20]))
    accepted, rejected = [], []
    for row in rows:
        reason = None
        if row.get('extraction_status') != 'EXTRACTED':
            reason = 'BODY_UNAVAILABLE'
        elif not official_url(row.get('final_url', row['url']), hosts):
            reason = 'REDIRECT_OUTSIDE_OFFICIAL_HOSTS'
        else:
            try:
                published = date.fromisoformat((row.get('article_published') or '')[:10])
            except ValueError:
                reason = 'PUBLICATION_DATE_UNAVAILABLE'
            else:
                if not since <= published <= until:
                    reason = 'OUTSIDE_DATE_WINDOW'
        if reason:
            rejected.append({**row, 'rejection_reason': reason})
        elif len(accepted) < maximum and not any(
                r['body_sha256'] == row['body_sha256'] for r in accepted):
            accepted.append(row)
    return accepted, rejected


def evidence_targets(sources):
    targets, evidence = [], []
    for row in sources:
        # Capture actual extracted paragraphs. The DOM matcher must find the exact text.
        paragraphs = list(dict.fromkeys(p.strip() for p in row['text'].splitlines()
                                       if 80 <= len(p.strip()) <= 600))[:3]
        crops = []
        for index, text in enumerate(paragraphs, 1):
            name = f'paragraph-{index}'
            eid = row['id'] + ':' + name
            crops.append({'name': name, 'text': text})
            evidence.append({'id': eid, 'source_id': row['id'], 'text': text,
                             'source_url': row.get('final_url', row['url'])})
        if crops:
            targets.append({'id': row['id'], 'url': row.get('final_url', row['url']),
                            'viewport_only': True, 'crops': crops})
    return targets, evidence


def bind_evidence(script, pack, evidence):
    project = normalize_script(script, pack)
    by_id = {e['id']: e for e in evidence}
    used = set()
    for line in project['lines']:
        eid = line.get('evidence_id')
        if eid not in by_id or by_id[eid]['source_id'] != line['source_id']:
            raise ValueError('Every line must reference captured evidence from its source')
        line['visual'] = eid
        used.add(eid)
    translations = script.get('translations', {})
    if not isinstance(translations, dict):
        raise ValueError('translations must map evidence IDs to faithful Japanese text')
    for eid in used:
        if not isinstance(translations.get(eid), str) or not translations[eid].strip():
            raise ValueError('Missing evidence translation: ' + eid)
    project['visuals'] = [{**by_id[eid]['visual'], 'translation': translations[eid]}
                          for eid in sorted(used)]
    return project


def prepare_trending(request, output, *, allow_api=False, executable_path=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError('Use a new empty preparation output folder')
    started = time.monotonic()
    timing = {'started_utc': datetime.now(timezone.utc).isoformat(), 'stages': [],
              'status': 'FAILED', 'clock': 'monotonic', 'scope': 'prepare_trending_only'}
    try:
        atomic_json(output/'request.json', request)
        hosts = request.get('official_hosts', [])
        if not hosts or any('/' in h or not h or h != h.lower() for h in hosts):
            raise ValueError('Explicit lowercase official_hosts are required')
        until = date.fromisoformat(request.get('as_of', datetime.now(timezone.utc).date().isoformat()))
        days = int(request.get('days', 7))
        if not 1 <= days <= 30:
            raise ValueError('days must be between 1 and 30')
        since = until - timedelta(days=days-1)
        with measured_stage(timing, 'research'):
            pack = research(request)
        timing['research_breakdown_seconds'] = pack.get('timing_seconds', {})
        atomic_json(output/'discovery.json', pack)
        with measured_stage(timing, 'official_sources_and_dates'):
            sources, rejected = select_official(pack, hosts, since, until)
        pack = {**pack, 'sources': sources, 'rejected_sources': rejected,
                'date_window': {'since': str(since), 'until': str(until)}}
        atomic_json(output/'research.json', pack)
        if not sources:
            raise ValueError('No dated official article in the requested window')
        targets, evidence = evidence_targets(sources)
        with measured_stage(timing, 'browser_capture'):
            captures = asyncio.run(capture_targets(targets, output/'materials',
                executable_path=executable_path, max_workers=4, retry_delays=()))
        capture_map = {c['id']: c for c in captures}
        usable = []
        for item in evidence:
            captured = capture_map[item['source_id']]
            if captured.get('error'):
                continue
            crop = next(c for c in captured['crops'] if item['id'].endswith(':'+c['name']))
            if captured.get('error') or not crop.get('found'):
                continue
            source = next(s for s in sources if s['id'] == item['source_id'])
            item['visual'] = {'id': item['id'], 'file': 'materials/'+crop['file'],
                'source_url': item['source_url'], 'source_credit': source['title'],
                'captured_utc': captured['captured_utc'], 'sha256': crop['sha256'],
                'source_language': 'en' if source.get('language') == 'en' else None}
            usable.append(item)
        atomic_json(output/'evidence.json', usable)
        if not usable:
            raise ValueError('No source paragraph was successfully captured; model not called')
        # Only successfully photographed text reaches the model, rather than whole unseen pages.
        eligible = []
        for source in sources:
            texts = [e['text'] for e in usable if e['source_id'] == source['id']]
            if texts:
                eligible.append({**source, 'text': '\n\n'.join(texts)})
        prompt_pack = {**pack, 'sources': eligible}
        prompt = script_prompt(prompt_pack, request.get('minutes', 3))
        prompt['system'] += (' 全台詞に資料IDに加えevidence_idを指定する。'
            '台詞の主張は指定された撮影済み段落だけで裏付ける。情報が足りなければ短くする。'
            'translationsオブジェクトに使用したevidence_idごとの撮影段落全文の忠実な日本語訳を返す。'
            '日本語段落は原文を保持する。ずんだもんは「なのだ」、四国めたんは「よ・わ」の口調。')
        data = json.loads(prompt['user'])
        data.update(date_window=pack['date_window'], evidence=[{k: v for k, v in e.items() if k != 'visual'} for e in usable])
        prompt['user'] = json.dumps(data, ensure_ascii=False)
        atomic_json(output/'script-prompt.json', prompt)
        if request.get('provider', 'none') == 'none':
            result = {'status': 'EVIDENCE_READY', 'screenshots': len(usable), 'script_generated': False}
        else:
            with measured_stage(timing, 'script_generation_including_cli'):
                script = generate_script(prompt, request['provider'], request.get('model'), allow_api=allow_api)
            atomic_json(output/'script-response.json', script)
            with measured_stage(timing, 'validate_and_save'):
                project = bind_evidence(script, prompt_pack, usable)
                atomic_json(output/'project.json', project)
                load_project(output/'project.json')
                (output/'script.md').write_text('# '+project['title']+'\n\n'+ '\n\n'.join(
                    f"**{r['speaker']}**：{r['text']}\n\n出典：{next(e['source_url'] for e in usable if e['id'] == r['evidence_id'])}"
                    for r in project['lines'])+'\n')
            result = {'status': 'PROJECT_READY', 'sources': len(eligible),
                      'screenshots': len(project['visuals']), 'lines': len(project['lines']),
                      'provider': request['provider'], 'video_created': False}
        atomic_json(output/'preparation.json', result)
        timing['status'] = result['status']
        return result
    except Exception as exc:
        atomic_json(output/'preparation.json', {'status': 'FAILED', 'error': type(exc).__name__})
        raise
    finally:
        timing.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                      total_seconds=round(time.monotonic()-started, 6))
        atomic_json(output/'timing.json', timing)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-api', action='store_true')
    parser.add_argument('--chromium')
    args = parser.parse_args()
    print(json.dumps(prepare_trending(json.loads(args.request.read_text()), args.output,
        allow_api=args.allow_api, executable_path=args.chromium), ensure_ascii=False))
