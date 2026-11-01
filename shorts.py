#!/usr/bin/env python3
"""Completed episode folder -> vertical short re-rendered from native layers.

The short is not a crop of the 360p episode. It re-draws the approved
character parts, the source visual and karaoke captions at 1080x1920 from the
episode's measured VOICEVOX audio, so no speaker tracking (reframe) is needed.
Video is encoded once (raw frames -> H.264); the audio is muxed with
``-c:v copy`` exactly like the main pipeline. No VOICEVOX engine is started.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import uuid
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'renderer'))
import render_reusable_landscape_raw as landscape  # noqa: E402  (registers the extra expressions)
from media_editing import mux_audio  # noqa: E402
from subtitle_tools import phrase_breaks, wrap_japanese  # noqa: E402

SPEAKERS = ('ずんだもん', '四国めたん')
CHARACTER_DIRS = {'ずんだもん': 'Zundamon', '四国めたん': 'Metan'}
LINE_RANGE = re.compile(r'^\s*(\d+)\s*(?:-\s*(\d+))?\s*$')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# ---------------------------------------------------------------- segment

def parse_lines(spec):
    """'12-20' or '12' -> (first_id, last_id)."""
    match = LINE_RANGE.match(str(spec))
    if not match:
        raise ValueError('--lines must look like 12-20')
    first = int(match.group(1))
    last = int(match.group(2) or first)
    if last < first:
        raise ValueError('--lines end must not precede start')
    return first, last


def select_segment(records, min_seconds=15, max_seconds=59, lines=None):
    """Return (first_index, last_index) of contiguous measured lines.

    Explicit ``lines`` wins. Otherwise the longest window that fits
    ``max_seconds`` is chosen, preferring windows that start where a section
    begins, so the short opens on a new topic instead of mid-conversation.
    """
    if not records:
        raise ValueError('Episode has no measured lines')
    if lines:
        first, last = parse_lines(lines)
        ids = [r['id'] for r in records]
        if first not in ids or last not in ids:
            raise ValueError(f'Line ids {first}-{last} are not in timing.json')
        i, j = ids.index(first), ids.index(last)
        if j < i:
            raise ValueError('--lines must follow the episode order')
        duration = records[j]['end'] - records[i]['start']
        if duration > max_seconds + 1e-6:
            raise ValueError(f'Selected lines last {duration:.1f}s; limit is {max_seconds}s')
        return i, j
    best = None
    for i, row in enumerate(records):
        section_start = i == 0 or row.get('scene_id') != records[i - 1].get('scene_id')
        for j in range(i, len(records)):
            duration = records[j]['end'] - row['start']
            if duration > max_seconds + 1e-6:
                break
            if duration < min_seconds:
                continue
            score = (section_start, round(duration, 3), -i)
            if best is None or score > best[0]:
                best = (score, i, j)
    if best is None:
        raise ValueError(f'No contiguous lines fit {min_seconds}-{max_seconds}s; pass --lines')
    return best[1], best[2]


# ---------------------------------------------------------------- karaoke timing

def phrases(text):
    """BudouX phrases with numbers, units and Latin words kept whole."""
    cuts = [0, *phrase_breaks(text), len(text)]
    return [text[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]


def progress_points(row, words):
    """Piecewise-linear (time, spoken fraction) inside one measured line.

    With recognized word timestamps the spoken fraction follows the speech,
    including pauses; without them it is character-weighted over the measured
    interval. Recognized text may differ from the script, so this aligns
    progress, not individual phonemes.
    """
    start, end = float(row['start']), float(row['end'])
    assigned = [w for w in words if start <= (w['start'] + w['end']) / 2 < end and str(w.get('text', ''))]
    if not assigned:
        return [(start, 0.0), (end, 1.0)], 'character_weighted'
    total = sum(len(str(w['text'])) for w in assigned)
    points, spoken, cursor = [(start, 0.0)], 0, start
    for word in assigned:
        begin = min(end, max(cursor, float(word['start'])))
        finish = min(end, max(begin, float(word['end'])))
        points.append((begin, spoken / total))
        spoken += len(str(word['text']))
        points.append((finish, spoken / total))
        cursor = finish
    points.append((end, 1.0))
    return points, 'recognized_word_timestamps'


def time_at(points, fraction):
    """Earliest time at which ``fraction`` of the line has been spoken."""
    if fraction <= 0:
        return points[0][0]
    for (t0, f0), (t1, f1) in zip(points, points[1:]):
        if f1 >= fraction:
            if f1 == f0:
                return t0
            return t0 + (t1 - t0) * (fraction - f0) / (f1 - f0)
    return points[-1][0]


def _split_long(piece, font, width):
    parts, rest = [], piece
    while font.getlength(rest) > width and len(rest) > 1:
        cut = max(n for n in range(1, len(rest) + 1) if font.getlength(rest[:n]) <= width or n == 1)
        parts.append(rest[:cut])
        rest = rest[cut:]
    return parts + [rest]


def karaoke_pages(row, words, font, width, max_lines):
    """Pages of at most ``max_lines`` lines; each line is [(phrase, start_time)].

    Every script character is kept; nothing is shrunk or dropped.
    """
    text = str(row['caption_text'])
    points, basis = progress_points(row, words)
    units, offset = [], 0
    for phrase in phrases(text):
        for piece in _split_long(phrase, font, width):
            units.append((piece, time_at(points, offset / len(text))))
            offset += len(piece)
    lines, line, used = [], [], 0.0
    for piece, begin in units:
        size = font.getlength(piece)
        if line and used + size > width:
            lines.append(line)
            line, used = [], 0.0
        line.append((piece, begin))
        used += size
    if line:
        lines.append(line)
    pages = [lines[n:n + max_lines] for n in range(0, len(lines), max_lines)]
    if ''.join(p for page in pages for ln in page for p, _ in ln) != text:
        raise ValueError('Karaoke layout must preserve every character')
    return pages, basis


def page_windows(row, pages):
    """(start, end) of each page: a page appears when its first phrase starts."""
    starts = [float(row['start'])] + [page[0][0][1] for page in pages[1:]]
    ends = starts[1:] + [float(row['end'])]
    return list(zip(starts, ends))


# ---------------------------------------------------------------- subtitles

def _ass_color(value):
    import pysubs2
    value = value.lstrip('#')
    return pysubs2.Color(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def write_karaoke_subtitles(cues, layout, width, height, output):
    """ASS with \\kf per phrase (editing), SRT/VTT per page (upload)."""
    import pysubs2
    karaoke = pysubs2.SSAFile()
    karaoke.info.update(PlayResX=str(width), PlayResY=str(height), WrapStyle='2')
    for speaker in SPEAKERS:
        karaoke.styles[speaker] = pysubs2.SSAStyle(
            fontname='Zen Maru Gothic Medium', fontsize=layout['caption_font_size'],
            primarycolor=_ass_color(layout['caption_text_colors'][speaker]),
            secondarycolor=_ass_color(layout['caption_upcoming_color']),
            outlinecolor=_ass_color('#FFFFFF'), outline=3, shadow=0, alignment=pysubs2.Alignment.MIDDLE_CENTER,
            marginl=60, marginr=60, marginv=0)
    plain = pysubs2.SSAFile()
    for cue in cues:
        begin, finish = round(cue['start'] * 1000), max(round(cue['end'] * 1000), round(cue['start'] * 1000) + 1)
        parts = []
        for index, line in enumerate(cue['lines']):
            parts.extend(line)
            if index < len(cue['lines']) - 1:
                parts.append(('\\N', None))
        text, timed = [], [p for p in parts if p[1] is not None]
        # Each \kf lasts until the next phrase starts (centiseconds).
        boundaries = [p[1] for p in timed] + [cue['end']]
        k = 0
        for piece, piece_start in parts:
            if piece_start is None:
                text.append(piece)
                continue
            duration = max(1, round((boundaries[k + 1] - boundaries[k]) * 100))
            text.append('{\\kf%d}%s' % (duration, piece.replace('{', '｛').replace('}', '｝')))
            k += 1
        lead = max(0, round((timed[0][1] - cue['start']) * 100)) if timed else 0
        body = ('{\\k%d}' % lead if lead else '') + ''.join(text)
        karaoke.append(pysubs2.SSAEvent(start=begin, end=finish, style=cue['speaker'], name=cue['speaker'], text=body))
        event = pysubs2.SSAEvent(start=begin, end=finish, name=cue['speaker'])
        event.plaintext = '\n'.join(''.join(p for p, _ in line) for line in cue['lines'])
        plain.append(event)
    karaoke.save(str(output / 'short.karaoke.ass'), encoding='utf-8')
    plain.save(str(output / 'short.srt'), encoding='utf-8')
    plain.save(str(output / 'short.vtt'), encoding='utf-8')


# ---------------------------------------------------------------- drawing

def _font(path, size):
    return ImageFont.truetype(str(path), size)


def fit_title(text, font_path, layout, width):
    for size in range(layout['title_font_size'], layout['title_min_font_size'] - 1, -2):
        face = _font(font_path, size)
        lines = wrap_japanese(text, face, width)
        if len(lines) <= layout['title_max_lines']:
            return face, lines
    raise ValueError('Title does not fit two lines; pass a shorter --title')


def build_backgrounds(presentation, title, font_path, layout, width, height, base_dir):
    visuals = presentation.get('visuals') or []
    if not visuals:
        raise ValueError('presentation.json has no visuals')
    face, title_lines = fit_title(title, font_path, layout, width - 80)
    small = _font(font_path, layout['source_font_size'])
    url_font = _font(font_path, layout['source_url_font_size'])
    translation_font = _font(font_path, layout['translation_font_size'])
    x1, y1, x2, y2 = layout['evidence']
    backgrounds, hashes = {}, {}
    for item in visuals:
        source = Path(item['file'])
        if not source.is_absolute():
            source = base_dir / source
        hashes[item['id']] = sha256(source)
        frame = Image.new('RGB', (width, height), layout['background'])
        draw = ImageDraw.Draw(frame)
        draw.rectangle(tuple(layout['header']), fill=layout['header_background'])
        line_h = face.size + 14
        top = (layout['header'][3] - line_h * len(title_lines)) // 2
        for n, line in enumerate(title_lines):
            draw.text((width / 2, top + n * line_h + line_h / 2), line, font=face, fill=layout['title_color'], anchor='mm')
        draw.rounded_rectangle((x1, y1, x2, y2), radius=28, fill='white')
        translation = str(item.get('translation') or '')
        box_bottom = y2 - (60 if translation else 0)
        with Image.open(source) as opened:
            visual = ImageOps.contain(opened.convert('RGB'), (x2 - x1 - 40, box_bottom - y1 - 40), Image.Resampling.LANCZOS)
        frame.paste(visual, ((width - visual.width) // 2, y1 + 20 + (box_bottom - y1 - 40 - visual.height) // 2))
        if translation:
            lines = wrap_japanese(translation, translation_font, x2 - x1 - 40)
            if len(lines) > 1:
                raise ValueError(f'Translation for {item["id"]} needs one line in a short')
            draw.rectangle((x1, y2 - 60, x2, y2), fill='#FFFFFF', outline='#2B6146', width=2)
            draw.text((width / 2, y2 - 30), lines[0], font=translation_font, fill='#17202F', anchor='mm')
        credit = str(item.get('source_label') or item.get('source_credit') or '利用者提供素材')
        url = landscape._display_source_url(item.get('source_url', ''))
        if small.getlength(credit) > width - 80:
            credit = wrap_japanese(credit, small, width - 80)[0]
        draw.text((width / 2, layout['source_y']), credit, font=small, fill='#425570', anchor='mt')
        if url and not url.startswith('user-provided'):
            draw.text((width / 2, layout['source_url_y']), url, font=url_font, fill='#4C5870', anchor='mt')
        draw.text((width / 2, layout['voice_credit_y']), presentation.get('voice_credit') or 'VOICEVOX:ずんだもん・四国めたん',
                  font=small, fill='#4C5870', anchor='mt')
        backgrounds[item['id']] = frame
    return backgrounds, hashes


def draw_caption(layout, font, label_font, speaker, lines, now):
    x1, y1, x2, y2 = layout['caption']
    plate = Image.new('RGBA', (x2 - x1, y2 - y1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, plate.width - 1, plate.height - 1), radius=30,
                           fill=layout['caption_backplates'][speaker], outline=layout['caption_outline'][speaker], width=4)
    draw.text((28, 18), speaker, font=label_font, fill=layout['caption_text_colors'][speaker])
    line_h = layout['caption_line_height']
    top = 60 + (plate.height - 60 - line_h * len(lines)) // 2
    for n, line in enumerate(lines):
        x = (plate.width - sum(font.getlength(p) for p, _ in line)) / 2
        for piece, begin in line:
            color = layout['caption_text_colors'][speaker] if now >= begin else layout['caption_upcoming_color']
            draw.text((x, top + n * line_h), piece, font=font, fill=color)
            x += font.getlength(piece)
    return plate


# ---------------------------------------------------------------- main

def load_episode(episode):
    episode = Path(episode).expanduser().resolve()
    needed = ['timing.json', 'narration.wav', 'presentation.json']
    missing = [name for name in needed if not (episode / name).is_file()]
    if missing:
        raise FileNotFoundError(f'Not a completed episode folder ({", ".join(missing)} missing): {episode}')
    state = read_json(episode / 'state.json') if (episode / 'state.json').is_file() else {}
    if state and state.get('status') != 'COMPLETE':
        raise ValueError(f'Episode status is {state.get("status")}, not COMPLETE')
    words = []
    if (episode / 'transcription.json').is_file():
        words = read_json(episode / 'transcription.json').get('words') or []
    effects = read_json(episode / 'sound-effects.json') if (episode / 'sound-effects.json').is_file() else []
    return {'dir': episode, 'state': state, 'records': read_json(episode / 'timing.json')['records'],
            'words': words, 'presentation': read_json(episode / 'presentation.json'), 'effects': effects}


def cut_wav(source, destination, start, end):
    with wave.open(str(source), 'rb') as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError('mono PCM16 narration required')
        rate = reader.getframerate()
        reader.setpos(int(round(start * rate)))
        frames = reader.readframes(int(round((end - start) * rate)))
    with wave.open(str(destination), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return np.frombuffer(frames, dtype='<i2').astype(np.float32) / 32768.0, rate


def make_short(args):
    started = time.monotonic()
    profile = read_json(args.profile)
    output_cfg, layout, segment_cfg = profile['output'], profile['layout'], profile['segment']
    width, height, fps = output_cfg['width'], output_cfg['height'], output_cfg['fps']
    acting = read_json(ROOT / 'config/landscape_profile_v18.json')['acting']
    max_seconds = float(args.max_seconds or segment_cfg['max_seconds'])
    if not 0 < max_seconds <= segment_cfg['hard_max_seconds']:
        raise ValueError(f'--max-seconds must be within 0-{segment_cfg["hard_max_seconds"]}')
    runtime = read_json(ROOT / 'runtime.local.json') if (ROOT / 'runtime.local.json').is_file() else {}
    shell = Path(args.shell or runtime.get('shell') or '')
    font_path = Path(args.font or runtime.get('font') or '')
    if not shell.is_dir() or not font_path.is_file():
        raise FileNotFoundError('Run setup first or pass --shell and --font (approved parts / Zen Maru Gothic)')

    episode = load_episode(args.episode)
    records = episode['records']
    first, last = select_segment(records, float(args.min_seconds or segment_cfg['min_seconds']), max_seconds, args.lines)
    chosen = records[first:last + 1]
    seg_start, seg_end = float(chosen[0]['start']), float(chosen[-1]['end'])
    duration = seg_end - seg_start
    for row in chosen:
        if row['speaker'] not in SPEAKERS or not row.get('caption_text'):
            raise ValueError(f'Line {row.get("id")}: unknown speaker or empty caption')

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'short.mp4').exists():
        raise FileExistsError('Will not replace an existing short.mp4; use a new --out')

    presentation = episode['presentation']
    title = args.title or presentation.get('title') or ''
    backgrounds, visual_hashes = build_backgrounds(presentation, title, font_path, layout, width, height, episode['dir'])
    default_visual = presentation['visuals'][0]['id']

    caption_font = _font(font_path, layout['caption_font_size'])
    label_font = _font(font_path, layout['caption_speaker_font_size'])
    cap_w = layout['caption'][2] - layout['caption'][0] - 80
    shifted, cues, bases = [], [], set()
    for row in chosen:
        local = {**row, 'start': row['start'] - seg_start, 'end': row['end'] - seg_start}
        local_words = [{**w, 'start': w['start'] - seg_start, 'end': w['end'] - seg_start} for w in episode['words']]
        pages, basis = karaoke_pages(local, local_words, caption_font, cap_w, layout['caption_max_lines'])
        bases.add(basis)
        local['pages'] = list(zip(page_windows(local, pages), pages))
        for (begin, finish), page in local['pages']:
            cues.append({'line_id': row['id'], 'speaker': row['speaker'], 'start': begin, 'end': finish, 'lines': page})
        shifted.append(local)
    write_karaoke_subtitles(cues, layout, width, height, out)

    audio_path = out / ('narration-' + uuid.uuid4().hex + '.wav')
    samples, rate = cut_wav(episode['dir'] / 'narration.wav', audio_path, seg_start, seg_end)

    variants, character_keys = {}, {}
    cache_root = Path(args.cache_root) if args.cache_root else ROOT / '.cache/characters'
    for name in SPEAKERS:
        variants[name], character_keys[name] = landscape.character_variants(shell / CHARACTER_DIRS[name], CHARACTER_DIRS[name], cache_root)

    silent = out / ('silent-' + uuid.uuid4().hex + '.mp4')
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}',
               '-r', str(fps), '-i', 'pipe:0', '-c:v', 'libx264', '-preset', output_cfg['preset'], '-crf', str(output_cfg['crf']),
               '-pix_fmt', 'yuv420p', '-threads', '4', '-movflags', '+faststart', str(silent)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    resized, plates = {}, {}
    mouth_seen = {name: set() for name in SPEAKERS}
    window = max(1, int(acting['window_seconds'] * rate))
    margin, bottom = layout['character_side_margin'], layout['character_bottom']
    current_visual, frames = default_visual, math.ceil(duration * fps)
    try:
        for index in range(frames):
            t = index / fps
            row = next((r for r in shifted if r['start'] <= t < r['end']), None)
            if row:
                current_visual = row.get('visual_id') if row.get('visual_id') in backgrounds else default_visual
            frame = backgrounds[current_visual].copy()
            chunk = samples[int(t * rate):int(t * rate) + window]
            rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
            mouth = 0 if rms < acting['closed_threshold'] else (1 if rms < acting['wide_threshold'] else 2)
            expression = (row.get('emotion') or 'NORMAL') if row else 'NORMAL'
            if expression not in acting['expressions']:
                expression = 'NORMAL'
            for name, side in (('四国めたん', 'left'), ('ずんだもん', 'right')):
                active = bool(row) and row['speaker'] == name
                state = mouth if active else 0
                expr = expression if active else ('SERIOUS' if expression == 'SERIOUS' else 'NORMAL')
                if active and expr == 'HAPPY' and t - row['start'] > acting['happiness_hold_seconds']:
                    expr = 'NORMAL'
                mouth_seen[name].add(state)
                key = (name, expr, state, active)
                if key not in resized:
                    target = layout['character_active_height'] if active else layout['character_inactive_height']
                    resized[key] = landscape.resize_character(variants[name][expr, state], target,
                                                              1.0 if active else layout['inactive_opacity'])
                image = resized[key]
                x = margin if side == 'left' else width - margin - image.width
                frame.paste(image, (x, bottom - image.height), image)
            if row:
                page_index, page = next(((n, p) for n, ((a, b), p) in enumerate(row['pages']) if a <= t < b),
                                        (len(row['pages']) - 1, row['pages'][-1][1]))
                spoken = sum(1 for line in page for _, begin in line if t >= begin)
                plate_key = (row['id'], page_index, spoken)
                if plate_key not in plates:
                    plates[plate_key] = draw_caption(layout, caption_font, label_font, row['speaker'], page, t)
                frame.paste(plates[plate_key], tuple(layout['caption'][:2]), plates[plate_key])
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        if process.wait():
            raise RuntimeError('Video encode failed')
        for name in {r['speaker'] for r in shifted}:
            if len(mouth_seen[name]) < 2:
                raise RuntimeError(f'{name}: no measured mouth change rendered')
        effects = []
        for effect in episode['effects']:
            begin = float(effect['start']) - seg_start
            if 0 <= begin < duration:
                effects.append({**effect, 'start': begin, 'duration': min(float(effect.get('duration', 1)), duration - begin)})
        settings = {'effects': effects}
        if args.bgm:
            settings.update(bgm=str(Path(args.bgm).resolve()), bgm_gain_db=args.bgm_gain_db)
        final_temp = out / ('final-' + uuid.uuid4().hex + '.mp4')
        mux_audio(silent, audio_path, final_temp, settings)
        final_temp.replace(out / 'short.mp4')
    finally:
        if process.poll() is None:
            process.kill()
        silent.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)

    description = episode['dir'] / 'description.txt'
    credits = description.read_text(encoding='utf-8').split('\n', 1)[1].strip() if description.is_file() else 'VOICEVOX:ずんだもん\nVOICEVOX:四国めたん'
    if args.bgm_credit:
        credits += '\n\nBGM: ' + args.bgm_credit
    (out / 'description.txt').write_text(f'{title}\n\n{credits}\n\n#Shorts\n', encoding='utf-8')
    report = {
        'status': 'COMPLETE', 'video': str(out / 'short.mp4'), 'video_sha256': sha256(out / 'short.mp4'),
        'size': [width, height], 'fps': fps, 'duration': round(duration, 3),
        'episode': str(episode['dir']), 'episode_video_sha256': episode['state'].get('video_sha256'),
        'narration_sha256': sha256(episode['dir'] / 'narration.wav'),
        'segment': {'start': round(seg_start, 3), 'end': round(seg_end, 3),
                    'line_ids': [r['id'] for r in chosen], 'selected_by': 'lines' if args.lines else 'auto_section_longest_fit'},
        'karaoke': {'unit': 'BudouX phrase', 'basis': sorted(bases),
                    'note': 'recognized word timestamps align spoken progress, not phonemes'},
        'reframe': 'not needed: re-rendered from native layers, speakers placed by timing.json',
        'voice_reused': True, 'engines_started_this_run': 0, 'new_synthesis_requests': 0, 'gpu_used': False,
        'mouth_states': {k: sorted(v) for k, v in mouth_seen.items()}, 'character_keys': character_keys,
        'visual_sha256': visual_hashes, 'bgm': bool(args.bgm), 'sound_effects': len(effects),
        'human_review': {'done': False, 'note': '公開前に人が視聴して切り出し位置・字幕を確認する（量産コンテンツ対策）'},
        'seconds': round(time.monotonic() - started, 3),
    }
    (out / 'short.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def add_arguments(parser):
    parser.add_argument('episode', type=Path, help='完成済みの run --out フォルダ')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--lines', help='台詞IDの範囲 例 12-20（省略時は自動選択）')
    parser.add_argument('--min-seconds', type=float)
    parser.add_argument('--max-seconds', type=float)
    parser.add_argument('--title', help='ショート用タイトル（省略時は本編タイトル）')
    parser.add_argument('--bgm', type=Path)
    parser.add_argument('--bgm-gain-db', type=float, default=-24)
    parser.add_argument('--bgm-credit', help='概要欄に載せるBGMクレジット')
    parser.add_argument('--shell', type=Path)
    parser.add_argument('--font', type=Path)
    parser.add_argument('--cache-root', type=Path)
    parser.add_argument('--profile', type=Path, default=ROOT / 'config/short_vertical_profile.json')


def main():
    parser = argparse.ArgumentParser(description='完成済みずんだもん回 → 縦型ショート（1080×1920・カラオケ字幕）')
    add_arguments(parser)
    make_short(parser.parse_args())


if __name__ == '__main__':
    main()
