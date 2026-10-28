"""Transcribe VOICEVOX narration once and use its word times for subtitles."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time
import wave
from gemini_route import request_target

MODEL = 'gemini-3.5-transcribe'
MAX_CHUNK_SECONDS = 1500  # word timestamps support up to 30 minutes per request
WORKERS = 5


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as out:
        name = out.name
        json.dump(value, out, ensure_ascii=False)
    os.replace(name, path)


def transcribe(audio_path, mime_type='audio/mpeg', *, api_key=None):
    url, headers = request_target(MODEL, api_key)
    audio = Path(audio_path).read_bytes()
    payload = {'contents': [{'role': 'user', 'parts': [{'inlineData': {
        'mimeType': mime_type, 'data': base64.b64encode(audio).decode('ascii')}}]}],
        'generationConfig': {'audioTranscriptionConfig': {
            'languageCodes': ['ja-JP'], 'wordTimestamp': True}}}
    request = Request(url,
                      data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json', **headers})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=240) as response:
                result = json.load(response)
            words = [word for candidate in result.get('candidates', [])
                     for part in candidate.get('content', {}).get('parts', [])
                     for word in part.get('audioTranscription', {}).get('words', [])]
            if not words:
                raise ValueError('no word timestamps returned')
            return {'words': words, 'attempts': attempt + 1,
                    'usage': result.get('usageMetadata', {})}
        except HTTPError as error:
            if error.code != 429 and not 500 <= error.code <= 599:
                raise RuntimeError(f'Gemini HTTP {error.code}') from None
            reason = f'HTTP {error.code}'
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            reason = type(error).__name__
        if attempt < 2:
            time.sleep(attempt + 1)
    raise RuntimeError(f'Gemini request failed after 3 attempts: {reason}')


def plan_chunks(records, duration, workers=WORKERS):
    """Cut at speech boundaries and keep each request under the API limit."""
    if duration <= 0:
        return []
    target = min(MAX_CHUNK_SECONDS, max(120.0, duration / max(1, workers)))
    boundaries = sorted({float(row['end']) for row in records if 0 < float(row['end']) < duration})
    spans, start = [], 0.0
    while duration - start > MAX_CHUNK_SECONDS:
        candidates = [end for end in boundaries if start < end <= start + MAX_CHUNK_SECONDS]
        end = min(candidates, key=lambda value: abs(value - (start + target))) if candidates else start + MAX_CHUNK_SECONDS
        spans.append((start, end))
        start = end
    while duration - start > target * 1.5:
        candidates = [end for end in boundaries if start < end < duration]
        if not candidates:
            break
        end = min(candidates, key=lambda value: abs(value - (start + target)))
        spans.append((start, end))
        start = end
    spans.append((start, duration))
    return spans


def _seconds(value):
    return float(str(value).removesuffix('s'))


def _append_words(records, words):
    """Attach recognized words to the measured VOICEVOX speaker intervals."""
    fallback = []
    for row in records:
        assigned = [w for w in words if row['start'] <= (w['start'] + w['end']) / 2 < row['end']]
        cues = []
        for word in assigned:
            start, end = max(row['start'], word['start']), min(row['end'], word['end'])
            if end <= start:
                continue
            text = word['text']
            # A very long recognized token is displayed in pieces without truncation.
            pieces = [text[i:i+20] for i in range(0, len(text), 20)]
            for i, piece in enumerate(pieces):
                begin = start + (end-start) * i / len(pieces)
                finish = start + (end-start) * (i+1) / len(pieces)
                space = ' ' if cues and cues[-1]['text'][-1:].isascii() and cues[-1]['text'][-1:].isalpha() and piece[:1].isascii() and piece[:1].isalpha() else ''
                if cues and len(cues[-1]['text'] + space + piece) <= 20:
                    cues[-1]['text'] += space + piece
                    cues[-1]['end'] = max(cues[-1]['end'], finish)
                else:
                    cues.append({'text': piece, 'start': begin, 'end': finish})
        if not cues:
            # Keep a usable caption when recognition missed a line. The report
            # explicitly records this; do not call it a recognized timestamp.
            fallback.append(row['id'])
            row.pop('caption_cues', None)
            row['caption_source'] = 'script_fallback'
        else:
            row['caption_cues'] = cues
            row['caption_source'] = 'gemini_transcribe'
    return fallback


def transcribe_narration(audio_path, records, *, cache_dir=None, workers=WORKERS, api_key=None):
    started = time.monotonic()
    audio_path = Path(audio_path)
    with wave.open(str(audio_path), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    digest = hashlib.sha256()
    with audio_path.open('rb') as audio:
        for block in iter(lambda: audio.read(1024 * 1024), b''):
            digest.update(block)
    audio_hash = digest.hexdigest()
    spans = plan_chunks(records, duration, workers)
    cache_dir = Path(cache_dir) if cache_dir else None

    def run_chunk(index_span):
        index, (start, end) = index_span
        identity = hashlib.sha256(f'{MODEL}:{audio_hash}:{start:.6f}:{end:.6f}'.encode()).hexdigest()
        cached = cache_dir / (identity + '.json') if cache_dir else None
        if cached and cached.is_file():
            try:
                saved = json.loads(cached.read_text(encoding='utf-8'))
                if saved.get('identity') == identity and saved.get('words'):
                    return {**saved, 'reused': True, 'attempts_this_run': 0}
            except (OSError, ValueError, TypeError):
                pass
        try:
            chunk_started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix='voicebox-transcribe-') as temporary:
                path = Path(temporary) / 'audio.mp3'
                subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(start),
                                '-t', str(end-start), '-i', str(audio_path), '-ac', '1',
                                '-ar', '16000', '-b:a', '48k', '-threads', '1', str(path)], check=True)
                response = transcribe(path, api_key=api_key)
            words = []
            for item in response['words']:
                try:
                    first = max(start, start + _seconds(item['startOffset']))
                    last = min(end, start + _seconds(item['endOffset']))
                    if item.get('word') and last > first:
                        words.append({'text': item['word'], 'start': first, 'end': last})
                except (KeyError, TypeError, ValueError):
                    continue
            if not words:
                raise ValueError('no usable word timestamps returned')
            result = {'identity': identity, 'index': index, 'start': start, 'end': end,
                      'words': words, 'seconds': round(time.monotonic()-chunk_started, 3),
                      'attempts_this_run': response['attempts'], 'usage': response['usage'], 'reused': False}
            if cached:
                try:
                    _atomic_json(cached, result)
                except OSError:
                    result['cache_unavailable'] = True
            return result
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            # No response body, auth value or original text goes into the error.
            return {'identity': identity, 'index': index, 'start': start, 'end': end,
                    'words': [], 'error': str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__,
                    'reused': False}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(spans) or 1))) as pool:
        chunks = list(pool.map(run_chunk, enumerate(spans)))
    words = sorted((word for chunk in chunks for word in chunk['words']), key=lambda word: word['start'])
    fallback = _append_words(records, words)
    return {'model': MODEL, 'audio_sha256': audio_hash, 'words': words,
            'chunks': [{key: value for key, value in chunk.items() if key not in ('words', 'identity')} for chunk in chunks],
            'fallback_line_ids': fallback, 'seconds': round(time.monotonic()-started, 3),
            'status': 'partial' if fallback or any(chunk.get('error') for chunk in chunks) else 'complete'}
