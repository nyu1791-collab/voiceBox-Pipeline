"""Transcribe a short audio file with Gemini 3.5 Transcribe."""
import base64
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from urllib.request import Request, urlopen

MODEL = 'gemini-3.5-transcribe'


def transcribe(audio_path, mime_type='audio/mpeg'):
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        raise ValueError('GEMINI_API_KEY is not configured')
    audio = Path(audio_path).read_bytes()
    # Larger recordings need the Files API, not an oversized inline request.
    if len(audio) > 15_000_000:
        raise ValueError('Audio is too large for inline upload')
    payload = {'contents': [{'parts': [{'inlineData': {
        'mimeType': mime_type, 'data': base64.b64encode(audio).decode('ascii')}}]}],
        'generationConfig': {'audioTranscriptionConfig': {
            'languageCodes': ['ja-JP'], 'wordTimestamp': True}}}
    request = Request('https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent',
                      data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
    try:
        with urlopen(request, timeout=240) as response:
            result = json.load(response)
    except Exception as error:
        raise RuntimeError(f'Gemini transcription failed ({getattr(error, "code", type(error).__name__)})') from None
    words = []
    for candidate in result.get('candidates', []):
        for part in candidate.get('content', {}).get('parts', []):
            words.extend(part.get('audioTranscription', {}).get('words', []))
    if not words:
        raise ValueError('Gemini returned no word timestamps')
    return words


def transcribe_narration(audio_path, records):
    """Use actual word timestamps, preserving the existing speaker intervals."""
    with wave.open(str(audio_path), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    words = []
    with tempfile.TemporaryDirectory(prefix='voicebox-transcribe-') as directory:
        offset = 0.0
        while offset < duration:
            length = min(1500.0, duration - offset)
            path = Path(directory) / 'audio.mp3'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(offset),
                            '-t', str(length), '-i', str(audio_path), '-ac', '1',
                            '-ar', '16000', '-b:a', '48k', str(path)], check=True)
            for item in transcribe(path, 'audio/mpeg'):
                start = float(str(item['startOffset']).removesuffix('s')) + offset
                end = float(str(item['endOffset']).removesuffix('s')) + offset
                if item.get('word') and end > start:
                    words.append({'text': item['word'], 'start': start, 'end': end})
            offset += length
    for row in records:
        assigned = [w for w in words if row['start'] <= (w['start'] + w['end']) / 2 < row['end']]
        cues = []
        for word in assigned:
            start, end = max(row['start'], word['start']), min(row['end'], word['end'])
            if end <= start:
                continue
            if cues and len(cues[-1]['text'] + word['text']) <= 20:
                cues[-1]['text'] += word['text']
                cues[-1]['end'] = max(cues[-1]['end'], end)
            else:
                cues.append({'text': word['text'], 'start': start, 'end': end})
        row['caption_cues'] = cues
    return words
