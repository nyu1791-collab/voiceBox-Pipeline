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
from image_search import collect_related_images


def prepare(request, output, script=None, *, allow_api=False, executable_path=None,
            regenerate_dialogue=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    gemini = script is None and request.get('provider') == 'gemini'
    saved_request = output / 'request.json'
    if any(output.iterdir()):
        previous_request = json.loads(saved_request.read_text()) if saved_request.is_file() else {}
        compatible = {k: v for k, v in previous_request.items() if k != 'minutes'} == {
            k: v for k, v in request.items() if k != 'minutes'}
        if not gemini or not compatible:
            raise FileExistsError('Use a new output folder for different inputs')
        receipt = output / 'preparation.json'
        if not regenerate_dialogue and receipt.is_file() and (output / 'project.json').is_file():
            previous = json.loads(receipt.read_text())
            if previous.get('status') == 'PROJECT_READY':
                return {**previous, 'reused': True, 'research_and_dialogue_calls': 0}
    started = time.monotonic()
    if gemini:
        if not saved_request.is_file():
            atomic_json(saved_request, request)
        result = prepare_gemini(request, output, started, executable_path=executable_path)
        atomic_json(saved_request, request)
        return result
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
        override = request.get('capture_overrides', {}).get(source['id'], {})
        specifications = {c['name']: c for c in override.get('crops', [])}
        for visual_id, item in choices:
            specification = specifications.get(item['name'], override)
            visuals.append({'id': visual_id, 'file': 'materials/' + item['file'],
                'source_url': source.get('final_url', source['url']), 'source_credit': source['title'],
                'captured_utc': capture['captured_utc'], 'sha256': item['sha256'],
                'source_language': specification.get('source_language', override.get('source_language')),
                'translation': specification.get('translation', '')})
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


def prepare_gemini(request, output, started, *, executable_path=None):
    from gemini_workflow import DEFAULT_MODEL, create_dialogue, research_with_gemini

    model = request.get('model') or DEFAULT_MODEL
    research_file = output / 'research.json'
    researched = not research_file.is_file()
    pack = research_with_gemini(request, model) if researched else json.loads(research_file.read_text())
    if researched:
        atomic_json(research_file, pack)
    targets = []
    for source in pack['sources']:
        override = request.get('capture_overrides', {}).get(source['id'], {})
        targets.append({'id': source['id'], 'url': source['url'], 'viewport_only': True,
                        'article_region': True,
                        'region_selector': override.get('selector') or ('#movie_player' if source['is_youtube'] else None),
                        'heading': override.get('heading'), 'crops': override.get('crops', [])})
    manifest = output / 'materials/manifest.json'
    images_manifest = output / 'materials/related/manifest.json'

    async def collect_materials():
        async def screenshots():
            if manifest.is_file():
                return json.loads(manifest.read_text())
            try:
                return await capture_targets(targets, output / 'materials', executable_path=executable_path,
                                             max_workers=4, retry_delays=())
            except Exception as error:
                return [{'id': target['id'], 'error': type(error).__name__} for target in targets]

        async def images():
            if images_manifest.is_file():
                return json.loads(images_manifest.read_text())
            result = await asyncio.to_thread(collect_related_images, request['topic'],
                                             output / 'materials/related', limit=request.get('max_images', 8))
            atomic_json(images_manifest, result)
            return result

        return await asyncio.gather(screenshots(), images())

    captures, image_results = asyncio.run(collect_materials())
    related = image_results['images']
    by_id = {capture['id']: capture for capture in captures}
    available, visuals, failures, assignments = [], [], [], {}
    for source in pack['sources']:
        capture = by_id.get(source['id'], {'error': 'capture unavailable'})
        found = [crop for crop in capture.get('crops', []) if crop.get('found')]
        preferred = next((crop for crop in found if crop['name'] != 'article'), None)
        preferred = preferred or next(iter(found), None)
        if not preferred and capture.get('files'):
            original = capture['files'][0]
            preferred = {'name': 'page', 'file': original['path'], 'sha256': original['sha256'],
                         'kind': 'page_playback_blocked' if capture.get('playback_blocked') else 'page'}
        if not preferred:
            failures.append({'id': source['id'], 'error': capture.get('error', 'capture unavailable')})
            continue
        source = {**source, 'screenshot': 'materials/' + preferred['file']}
        available.append(source)
        override = request.get('capture_overrides', {}).get(source['id'], {})
        specifications = {crop['name']: crop for crop in override.get('crops', [])}
        choices = [(source['id'], preferred)] + [
            (source['id'] + ':' + crop['name'], crop) for crop in found if crop['name'] != 'article']
        for visual_id, image in choices:
            specification = specifications.get(image['name'], override)
            visuals.append({'id': visual_id, 'file': 'materials/' + image['file'],
                            'source_url': source['url'], 'source_credit': source['title'],
                            'captured_utc': capture['captured_utc'], 'sha256': image['sha256'],
                            'capture_kind': image.get('kind', 'paragraph'),
                            'source_language': specification.get('source_language'),
                            'translation': specification.get('translation', '')})
            for line_id in specification.get('line_ids', []):
                assignments[line_id] = (source['id'], visual_id)
    if not available and not related:
        # A neutral background lets the conversation continue without pretending
        # that a failed capture is a source image.
        from PIL import Image
        fallback = output / 'materials' / 'neutral-background.png'
        fallback.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (1280, 720), (35, 56, 48)).save(fallback)
        visuals.append({'id': 'neutral-background', 'file': 'materials/neutral-background.png',
                        'source_credit': '背景', 'capture_kind': 'neutral_background'})

    captured = {source['id']: source for source in available}
    script_sources = [captured.get(source['id'], source) for source in pack['sources']]
    prompt = script_prompt({**pack, 'sources': script_sources}, request.get('minutes', 10))
    atomic_json(output / 'script-prompt.json', prompt)
    script, details = create_dialogue(prompt, script_sources, output, model, related_images=related)
    project = normalize_script(script, {**pack, 'sources': script_sources}, flexible=True)
    image_ids = {image['id'] for image in related}
    visual_ids = {visual['id'] for visual in visuals} | image_ids
    default_visual = available[0]['id'] if available else (related[0]['id'] if related else 'neutral-background')
    for line in project['lines']:
        if line.get('source_id') not in captured:
            line['visual'] = default_visual
            line['visual_context_only'] = True
        if line.get('visual_id') in visual_ids:
            line['visual'] = line['visual_id']
            line['visual_context_only'] = line['visual_id'] != line.get('source_id')
        assignment = assignments.get(line['id'])
        if assignment and line.get('source_id') == assignment[0]:
            line['visual'] = assignment[1]
    if related and not any(line['visual'] in image_ids for line in project['lines']):
        project['lines'][0]['visual'] = related[0]['id']
        project['lines'][0]['visual_context_only'] = True
    project['visuals'] = visuals + related
    project.setdefault('audio', {'normalize': True})
    atomic_json(output / 'project.json', project)
    result = {'status': 'PROJECT_READY', 'sources': len(pack['sources']),
              'screenshots': len({v['file'] for v in visuals if v.get('capture_kind') != 'neutral_background'}),
              'capture_failures': failures,
              'related_images': len(related), 'image_errors': image_results['errors'],
              'youtube_playback_blocked': sum(bool(c.get('playback_blocked')) for c in captures),
              'lines': len(project['lines']), 'provider': 'gemini', 'model': model,
              'thinking_level': 'medium', 'research_and_dialogue_calls': int(researched) + 1, **details,
              'seconds': round(time.monotonic()-started, 3), 'video_created': False}
    atomic_json(output / 'preparation.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--script', type=Path, help='Provided JSON script; no LLM call')
    parser.add_argument('--allow-api', action='store_true', help='Permit the selected non-Gemini script API')
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
