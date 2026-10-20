"""Collect sources, optionally generate JSON, and capture the exact referenced pages."""
from pathlib import Path
import argparse
import asyncio
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import atomic_json, load_project
from research import research, script_prompt, generate_script, normalize_script
from renderer.material_capture import capture_targets


def prepare(request, output, script=None, *, allow_api=False, executable_path=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FileExistsError('Use a new empty preparation output folder')
    started = time.monotonic()
    pack = research(request)
    atomic_json(output / 'research.json', pack)
    prompt = script_prompt(pack, request.get('minutes', 10))
    atomic_json(output / 'script-prompt.json', prompt)
    provider = request.get('provider', 'none')
    generated = script is None and provider != 'none'
    if script is None:
        if provider == 'none':
            result = {'status': 'RESEARCH_READY', 'sources': len(pack['sources']),
                      'seconds': round(time.monotonic()-started, 3), 'script_generated': False}
            atomic_json(output / 'preparation.json', result)
            return result
        script = generate_script(prompt, provider, request.get('model'), allow_api=allow_api)
    project = normalize_script(script, pack)
    selected_ids = {row['source_id'] for row in project['lines']}
    selected = [s for s in pack['sources'] if s['id'] in selected_ids]
    targets = []
    for source in selected:
        override = request.get('capture_overrides', {}).get(source['id'], {})
        targets.append({'id': source['id'], 'url': source.get('final_url', source['url']),
            'viewport_only': True, 'article_region': True,
            'region_selector': override.get('selector', request.get('capture_selector')),
            'heading': override.get('heading'), 'crops': override.get('crops', [])})
    # Bind explicit paragraph choices to dialogue before launching the browser.
    assignments = {}
    for target in targets:
        names = set()
        for crop in target['crops']:
            name = crop['name']
            if name in names or name == 'article':
                raise ValueError('Crop names must be unique and not article')
            names.add(name)
            for line_id in crop.get('line_ids', []):
                line = next((r for r in project['lines'] if r['id'] == line_id), None)
                if line is None or line['source_id'] != target['id']:
                    raise ValueError('Crop line_ids must reference dialogue using the same source')
                if line_id in assignments:
                    raise ValueError('Each dialogue line can select only one crop')
                assignments[line_id] = target['id'] + ':' + name
    captures = asyncio.run(capture_targets(targets, output / 'materials', executable_path=executable_path,
                                          max_workers=4, retry_delays=()))
    by_id = {c['id']: c for c in captures}
    visuals = []
    failures = []
    for source in selected:
        capture = by_id[source['id']]
        requested = [c for c in capture.get('crops', []) if c['name'] != 'article']
        crop = next((c for c in requested if c.get('found')), None) or next(
            (c for c in capture.get('crops', []) if c.get('found')), None)
        if capture.get('error') or not crop or any(not c.get('found') for c in requested):
            failures.append(source['id'])
            continue
        # Keep the original source ID as the backwards-compatible default image.
        choices = [(source['id'], crop)] + [
            (source['id'] + ':' + item['name'], item) for item in requested]
        for visual_id, item in choices:
            visuals.append({'id': visual_id, 'file': 'materials/' + item['file'],
                'source_url': source.get('final_url', source['url']), 'source_credit': source['title'],
                'captured_utc': capture['captured_utc'], 'sha256': item['sha256']})
    if failures:
        atomic_json(output / 'preparation.json', {'status': 'FAILED', 'capture_failures': failures})
        raise RuntimeError('Referenced page capture failed; see materials/manifest.json')
    for line in project['lines']:
        if line['id'] in assignments:
            line['visual'] = assignments[line['id']]
    project['visuals'] = visuals
    project.setdefault('audio', {'normalize': True})
    atomic_json(output / 'project.json', project)
    load_project(output / 'project.json')
    result = {'status': 'PROJECT_READY', 'sources': len(pack['sources']), 'screenshots': len({v['file'] for v in visuals}),
              'lines': len(project['lines']), 'provider': provider if generated else 'provided',
              'seconds': round(time.monotonic()-started, 3), 'video_created': False}
    atomic_json(output / 'preparation.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--script', type=Path, help='Provided JSON script; no LLM call')
    parser.add_argument('--allow-api', action='store_true', help='Explicitly permit the selected external script API')
    parser.add_argument('--chromium', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'))
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    script = json.loads(args.script.read_text()) if args.script else None
    if script:
        # search_keyword is discovery input; screenshots always use a resolved source URL.
        lines = script if isinstance(script, list) else script.get('lines', [])
        keywords = [r['search_keyword'] for r in lines if r.get('search_keyword')]
        if keywords:
            request['queries'] = list(dict.fromkeys(request.get('queries', []) + keywords))
        audio = script.get('audio', {}) if isinstance(script, dict) else {}
        bank = audio.get('se', {})
        def resolve(cue):
            cue = {'file': cue} if isinstance(cue, str) else dict(cue)
            cue['file'] = str((args.script.parent / bank.get(cue['file'], cue['file'])).resolve())
            return cue
        if audio.get('bgm'):
            audio['bgm'] = str((args.script.parent / audio['bgm']).resolve())
        if audio.get('transition_se'):
            audio['transition_se'] = resolve(audio['transition_se'])
        for line in lines:
            if line.get('se'):
                line['se'] = resolve(line['se'])
    result = prepare(request, args.output, script, allow_api=args.allow_api, executable_path=args.chromium)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
