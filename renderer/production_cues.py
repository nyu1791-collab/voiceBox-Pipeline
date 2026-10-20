"""Authored chapters and evidence-backed diagrams, bound to measured dialogue."""
from pathlib import Path
import copy
import json
from functools import lru_cache


def validate_direction(project):
    chapters = project.get('chapters', [])
    if not isinstance(chapters, list) or len(chapters) > 5:
        raise ValueError('chapters must contain at most five readable chapters')
    ids = set()
    for chapter in chapters:
        cid = chapter.get('id')
        if (not isinstance(cid, str) or not cid or cid in ids or cid in ('intro', 'closing')
                or not isinstance(chapter.get('title'), str) or not 1 <= len(chapter['title']) <= 18
                or not isinstance(chapter.get('summary'), str) or not 1 <= len(chapter['summary']) <= 42):
            raise ValueError('Each chapter needs a unique id, title (1–18) and summary (1–42)')
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
            or not 0 <= row['start'] < row['end'] for line, row in zip(project['lines'], records)):
        raise ValueError('Production cues must match measured dialogue ids, text and sections')
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
    sources, facts, lines, scenes = {}, {}, [], {}
    for line in project['lines']:
        visual = visuals[line['visual']]
        quote = visual.get('evidence_text')
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError('Motion graphics require saved original evidence_text for every source')
        source_id = visual.get('source_id', visual['id'])
        sources[source_id] = {'source_id': source_id, 'url': visual['source_url'],
                              'title': visual.get('source_credit', source_id)}
        fid = 'fact_'+visual['id']
        facts[fid] = {'fact_id': fid, 'source_id': source_id, 'quote': quote}
        sid = line.get('section', 'main')
        scenes.setdefault(sid, {'scene_id': sid, 'heading': sid})
        lines.append({'line_id': line['id'], 'text': line['text'], 'scene_id': sid,
                      'claim_type': 'reported' if line['speaker'] == '四国めたん' else 'question',
                      'fact_ids': [fid]})
    diagrams = copy.deepcopy(project['explainers'])
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


def cache_state(cues, t):
    chapter = next((c for c in cues.get('chapters', []) if c['start'] <= t < c['end']), None)
    overview = any(w['start'] <= t < w['end'] for w in cues.get('overview', []))
    return ((chapter['id'], int(100*(t-chapter['start'])/(chapter['end']-chapter['start'])))
            if chapter else None, overview)


@lru_cache(maxsize=24)
def _font(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


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
        progress = max(1, round(830*(t-current['start'])/(current['end']-current['start'])))
        draw.rectangle((225, 73, 225+progress, 79), fill='#2E9E6A')
    if not any(w['start'] <= t < w['end'] for w in cues.get('overview', [])):
        return
    draw.rounded_rectangle((225, 92, 1055, 490), radius=12, fill='#FAF8F3', outline='#2E9E6A', width=3)
    draw.text((246, 102), '今回のあらすじ', font=font(30), fill='#245B43')
    row_height = 330/max(1, len(chapters))
    def wrapped(value, face, width):
        lines, line = [], ''
        for char in value:
            if line and face.getlength(line+char) > width:
                lines.append(line); line = ''
            line += char
        return lines+[line]
    for i, chapter in enumerate(chapters):
        y = round(150+i*row_height)
        face = font(26)
        left = wrapped(f"第{i+1}章 {chapter['title']}", face, 260)
        right = wrapped(chapter['summary'], face, 500)
        if max(len(left), len(right))*31 > row_height-4:
            raise ValueError('Overview text is too long; shorten authored summaries without shrinking type')
        draw.line((242, y-5, 1038, y-5), fill='#CDDCD2', width=2)
        for n, text in enumerate(left): draw.text((246, y+n*31), text, font=face, fill='#245B43')
        for n, text in enumerate(right): draw.text((525, y+n*31), text, font=face, fill='#26332D')
