"""Authored chapters and evidence-backed diagrams, bound to measured dialogue."""
from pathlib import Path
import copy
import json
import math
from functools import lru_cache


def validate_direction(project):
    chapters = project.get('chapters', [])
    if not isinstance(chapters, list) or len(chapters) > 5:
        raise ValueError('chapters must contain at most five readable chapters')
    ids = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError('Each chapter must be an object')
        cid = chapter.get('id')
        if (not isinstance(cid, str) or not cid or cid in ids or cid in ('intro', 'closing')
                or not isinstance(chapter.get('title'), str) or not 1 <= len(chapter['title']) <= 18
                or not isinstance(chapter.get('summary'), str) or not 1 <= len(chapter['summary']) <= 42):
            raise ValueError('Each chapter needs a unique id, title (1–18) and summary (1–42)')
        if any(c in chapter['title']+chapter['summary'] for c in '\n\r\t'):
            raise ValueError('Chapter titles and summaries must be single-line text')
        ids.add(cid)
    if chapters:
        order = []; previous = None
        for line in project['lines']:
            sid = line.get('section')
            if sid not in ids | {'intro', 'closing'}:
                raise ValueError('Every line must belong to a declared chapter, intro or closing')
            if sid != previous:
                if sid in order:
                    raise ValueError('Chapter dialogue must form contiguous sections')
                order.append(sid); previous = sid
            if line.get('display') == 'overview' and sid != 'intro':
                raise ValueError('The overview table belongs to the spoken intro')
        if [sid for sid in order if sid in ids] != [c['id'] for c in chapters]:
            raise ValueError('Chapters must follow their authored order and contain dialogue')
        if 'intro' in order and order[0] != 'intro' or 'closing' in order and order[-1] != 'closing':
            raise ValueError('Intro and closing must bookend the chapters')


def compile_direction(project, records):
    validate_direction(project)
    if len(records) != len(project['lines']) or any(
            row['id'] != line['id'] or row.get('caption_text') != line['text']
            or row.get('scene_id') != line.get('section', 'main')
            or any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) for k in ('start','end'))
            or not 0 <= row['start'] < row['end'] for line, row in zip(project['lines'], records)):
        raise ValueError('Production cues must match measured dialogue ids, text and sections')
    if any(a['end'] > b['start'] for a,b in zip(records, records[1:])):
        raise ValueError('Measured dialogue must be ordered and non-overlapping')
    chapters = []
    for index, chapter in enumerate(project.get('chapters', []), 1):
        rows = [r for r in records if r['scene_id'] == chapter['id']]
        chapters.append({**chapter, 'index': index, 'start': rows[0]['start'], 'end': rows[-1]['end']})
    overview = [{'start': r['start'], 'end': r['end']} for line, r in zip(project['lines'], records)
                if line.get('display') == 'overview' and chapters]
    return {'chapters': chapters, 'overview': overview}


def attach_motion(project, records, presentation, output):
    """Bridge the standard project to the existing validated motion renderer."""
    if not project.get('explainers'):
        return
    from .motion_diagrams import compile_timeline, digest, plan_errors
    visuals = {v['id']: v for v in project['visuals']}
    diagrams = copy.deepcopy(project['explainers'])
    if not isinstance(diagrams, list) or any(not isinstance(d, dict) or not isinstance(d.get('elements'), list)
                                           for d in diagrams):
        raise ValueError('explainers must contain diagram objects with element lists')
    if any(not isinstance(e, dict) or type(e.get('line_id')) is not int
           for d in diagrams for e in d['elements']):
        raise ValueError('Explainer elements must reference integer dialogue ids')
    linked = {element.get('line_id') for d in diagrams for element in d['elements']}
    sources, facts, lines, scenes = {}, {}, [], {}
    for line in project['lines']:
        visual = visuals[line['visual']]
        quote = visual.get('evidence_text')
        has_quote = isinstance(quote, str) and bool(quote.strip())
        if line['id'] in linked and not has_quote:
            raise ValueError('Motion graphics require saved original evidence_text for referenced dialogue')
        source_id = visual.get('source_id', visual['id'])
        fid = 'fact_'+visual['id']
        if has_quote:
            url = visual.get('source_url')
            if not isinstance(url, str) or not url:
                raise ValueError('Evidence text requires an attributed source_url')
            if source_id in sources and sources[source_id]['url'] != url:
                raise ValueError('A source_id cannot identify different evidence URLs')
            sources[source_id] = {'source_id': source_id, 'url': url,
                                  'title': visual.get('source_credit', source_id)}
            facts[fid] = {'fact_id': fid, 'source_id': source_id, 'quote': quote}
        sid = line.get('section', 'main')
        scenes.setdefault(sid, {'scene_id': sid, 'heading': sid})
        lines.append({'line_id': line['id'], 'text': line['text'], 'scene_id': sid,
                      'claim_type': 'reported' if line['speaker'] == '四国めたん' else 'question',
                      'fact_ids': [fid] if has_quote else []})
    for diagram in diagrams:
        for element in diagram['elements']:
            line = next((l for l in project['lines'] if l['id'] == element['line_id']), None)
            if line is None:
                raise ValueError('Unknown explainer dialogue id')
            fid = 'fact_'+line['visual']
            element['fact_ids'] = [fid]
            if diagram['template'] == 'bars':
                element['value_fact_id'] = fid
    plan = {'schema': 'motion-plan-v1', 'diagrams': diagrams,
            'omitted_scenes': [s for s in scenes if s not in {d['scene_id'] for d in diagrams}]}
    pack = {'scenes': list(scenes.values()), 'sources': list(sources.values()),
            'facts': list(facts.values()), 'lines': lines, 'motion_plan': plan}
    errors = plan_errors(plan, pack)
    if errors:
        raise ValueError('\n'.join(errors))
    if records is None:
        return  # Input-only preflight: no guessed dialogue timestamps or written timeline.
    timeline = compile_timeline(plan, pack, records)
    timeline['source_sha256'] = digest(pack)
    output = Path(output)
    for name, value in [('motion-source.json', pack), ('motion-plan.json', plan)]:
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    presentation.update(motion_diagrams=timeline, motion_in_visual_slot=True,
                        motion_region=[225, 92, 1055, 482])


def _progress_width(chapter, t):
    return max(1, min(830, round(830*(t-chapter['start'])/(chapter['end']-chapter['start']))))


def cache_state(cues, t):
    chapter = next((c for c in cues.get('chapters', []) if c['start'] <= t < c['end']), None)
    overview = any(w['start'] <= t < w['end'] for w in cues.get('overview', []))
    return ((chapter['id'], _progress_width(chapter,t))
            if chapter else None, overview)


@lru_cache(maxsize=24)
def _font(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=128)
def _wrapped(value, path, size, width):
    face = _font(path, size)
    lines, line = [], ''
    for char in value:
        if line and face.getlength(line+char) > width:
            lines.append(line); line = ''
        line += char
    return tuple(lines+[line])


def _overview_rows(chapters, font_path):
    height = 330/max(1, len(chapters))
    rows = []
    for i,chapter in enumerate(chapters):
        left = _wrapped(f"第{i+1}章 {chapter['title']}", str(font_path), 26, 260)
        right = _wrapped(chapter['summary'], str(font_path), 26, 500)
        if max(len(left),len(right))*31 > height-4:
            raise ValueError('Overview text is too long; shorten authored summaries without shrinking type')
        rows.append((round(150+i*height), left, right))
    return rows


def validate_direction_layout(project, font_path):
    """Check authored typography before expensive synthesis or video encoding."""
    validate_direction(project)
    chapters = project.get('chapters', [])
    for i,chapter in enumerate(chapters,1):
        text = f"第{i}章 / 全{len(chapters)}章　{chapter['title']}"
        if _font(str(font_path),30).getlength(text) > 827:
            raise ValueError('Chapter heading is too wide for the production header')
    if any(line.get('display') == 'overview' for line in project['lines']):
        _overview_rows(chapters,font_path)


def draw_direction(frame, cues, t, font_path):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(frame)
    font = lambda size: _font(str(font_path), size)
    chapters = cues.get('chapters', [])
    current = next((c for c in chapters if c['start'] <= t < c['end']), None)
    if current:
        draw.rectangle((215, 18, 1065, 84), fill='#F3F0E6')
        draw.text((228, 25), f"第{current['index']}章 / 全{len(chapters)}章　{current['title']}",
                  font=font(30), fill='#245B43')
        draw.rectangle((225, 73, 1055, 79), fill='#D7E4DC')
        progress = _progress_width(current,t)
        draw.rectangle((225, 73, 225+progress, 79), fill='#2E9E6A')
    if not any(w['start'] <= t < w['end'] for w in cues.get('overview', [])):
        return
    draw.rounded_rectangle((225, 92, 1055, 490), radius=12, fill='#FAF8F3', outline='#2E9E6A', width=3)
    draw.text((246, 102), '今回のあらすじ', font=font(30), fill='#245B43')
    for y,left,right in _overview_rows(chapters,font_path):
        face = font(26)
        draw.line((242, y-5, 1038, y-5), fill='#CDDCD2', width=2)
        for n, text in enumerate(left): draw.text((246, y+n*31), text, font=face, fill='#245B43')
        for n, text in enumerate(right): draw.text((525, y+n*31), text, font=face, fill='#26332D')
