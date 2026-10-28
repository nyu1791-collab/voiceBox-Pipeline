"""Small CPU-only Japanese phrase layout and editable subtitle delivery."""
from functools import lru_cache
from pathlib import Path
import json
import math
import re

OPEN = '「『（【〈《([{'
CLOSE = '、。！？：；」』）】〉》)]},.!?:;'
PROTECTED = re.compile(r'[0-9０-９]+(?:[.,．][0-9０-９]+)*[ \u3000]*(?:兆円|億円|万円|時間|ドル|％|%|円|年|月|日|秒|分|人|件|倍|GB|MB|Hz|px|fps)|'
                       r'[A-Za-z0-9]+(?:[._:/+%-][A-Za-z0-9]+)*')


@lru_cache(maxsize=1)
def japanese_parser():
    import budoux
    return budoux.load_default_japanese_parser()


@lru_cache(maxsize=8192)
def phrase_breaks(text, emphasis=()):
    chunks = japanese_parser().parse(text)
    if ''.join(chunks) != text:
        raise ValueError('Phrase layout must preserve every character')
    protected = set()
    for match in PROTECTED.finditer(text):
        protected.update(range(match.start()+1, match.end()))
    for term in emphasis:
        if not term:
            continue
        begin = text.find(term)
        while begin >= 0:
            protected.update(range(begin+1, begin+len(term)))
            begin = text.find(term, begin+1)
    positions, offset = [], 0
    for chunk in chunks[:-1]:
        offset += len(chunk)
        if offset not in protected and text[offset] not in CLOSE and text[offset-1] not in OPEN:
            positions.append(offset)
    return tuple(positions)


def wrap_japanese(text, font, width, strict=False):
    text = str(text)
    boundaries = phrase_breaks(text)
    result, start = [], 0
    while start < len(text):
        if font.getlength(text[start:]) <= width:
            result.append(text[start:])
            break
        candidates = [p for p in boundaries if p > start and font.getlength(text[start:p]) <= width]
        if not candidates:
            # A very long unbroken word or URL still needs to fit on screen.
            low, high = start + 1, len(text)
            while low < high:
                mid = (low + high + 1) // 2
                if font.getlength(text[start:mid]) <= width:
                    low = mid
                else:
                    high = mid - 1
            candidates = [low]
        end = candidates[-1]
        result.append(text[start:end])
        start = end
    return result


def transcript_caption_at(row, absolute_time):
    """Return the same transcript cue text used by the subtitle files."""
    return next((cue['text'] for cue in row['caption_cues']
                 if cue['start'] <= absolute_time < cue['end']), '')


def write_subtitles(records, font, layout, output):
    """Export the same Gemini-timed captions used by the video."""
    import pysubs2
    from renderer.render_reusable_landscape_raw import caption_states, wrap_words, _font
    output = Path(output)
    document = pysubs2.SSAFile()
    document.info.update(PlayResX='1280', PlayResY='720', WrapStyle='2')
    for speaker, color in [('ずんだもん', pysubs2.Color(32, 72, 48)),
                           ('四国めたん', pysubs2.Color(92, 38, 64))]:
        document.styles[speaker] = pysubs2.SSAStyle(fontname='Zen Maru Gothic Medium', fontsize=36,
            primarycolor=color, outline=0, shadow=0, marginl=260, marginr=260, marginv=34)
    cues = []
    face = _font(str(font), 36)
    width = int(layout.get('caption_box_width', 760))-2*int(layout.get('caption_box_pad', 14))-24
    for row in records:
        start, end = float(row['start']), float(row['end'])
        if not all(map(math.isfinite, (start, end))) or not 0 <= start < end:
            raise ValueError('Subtitle timing must use a positive measured voice interval')
        if cues and start*1000 < cues[-1]['end_ms']-1:
            raise ValueError('Subtitle voice intervals overlap')
        if 'caption_cues' in row:
            timed = [(cue['text'], round(cue['start']*1000), round(cue['end']*1000))
                     for cue in row['caption_cues']]
        else:
            states = caption_states(row['caption_text'], font, layout, row.get('caption_emphasis_terms') or ())
            if not states or ''.join(states) != row['caption_text']:
                raise ValueError('Subtitle states must preserve the full caption text')
            total, offset, timed = sum(map(len, states)), 0, []
            for state in states:
                begin = round((start+(end-start)*offset/total)*1000)
                offset += len(state)
                timed.append((state, begin, round((start+(end-start)*offset/total)*1000)))
        for state, begin, finish in timed:
            if finish <= begin:
                raise ValueError('Subtitle interval is too short for millisecond output')
            lines = wrap_words(state, face, width, strict=True)
            if lines is None or len(lines) > 3:
                raise ValueError('Editable subtitle exceeds the approved layout')
            # Metadata retains exact plain text even where a subtitle format has its own escaping.
            event = pysubs2.SSAEvent(start=begin, end=finish, style=row['speaker'], name=row['speaker'])
            event.plaintext = '\n'.join(lines)
            document.append(event)
            cues.append({'line_id': row['id'], 'speaker': row['speaker'], 'start_ms': begin,
                         'end_ms': finish, 'text': state, 'lines': lines})
    for suffix in ('srt', 'ass', 'vtt'):
        options = {'keep_ssa_tags': True} if suffix in ('srt', 'vtt') else {}
        document.save(str(output / ('subtitles.'+suffix)), encoding='utf-8', **options)
    aligned = any('caption_cues' in row for row in records)
    (output / 'subtitles.json').write_text(json.dumps({'timing_basis': 'Gemini word timestamps; script fallback where needed' if aligned else 'measured voice intervals; character-weighted caption states',
        'word_alignment': aligned, 'fallback_line_ids': [row['id'] for row in records if row.get('caption_source') == 'script_fallback'],
        'cues': cues}, ensure_ascii=False, indent=2)+'\n')
    return cues
