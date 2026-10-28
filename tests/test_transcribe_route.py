import json
import os
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO

from gemini_transcribe import plan_chunks, transcribe, transcribe_narration
from subtitle_tools import transcript_caption_at


class TranscriptionRouteTest(unittest.TestCase):
    def test_words_are_bound_to_existing_speakers(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'narration.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(bytes(8000 * 4))
            records = [{'id': 1, 'speaker': 'ずんだもん', 'caption_text': 'こんにちは', 'start': 0, 'end': 1},
                       {'id': 2, 'speaker': '四国めたん', 'caption_text': 'そうね', 'start': 1, 'end': 2}]
            words = [{'word': 'こんにちは', 'startOffset': '0.1s', 'endOffset': '0.8s'},
                     {'word': 'そうね', 'startOffset': '1.1s', 'endOffset': '1.8s'}]
            response = {'words': words, 'attempts': 1, 'usage': {}}
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test'}), \
                 patch('gemini_transcribe.subprocess.run'), \
                 patch('gemini_transcribe.transcribe', return_value=response):
                result = transcribe_narration(audio, records)
            self.assertEqual(records[0]['caption_cues'][0]['text'], 'こんにちは')
            self.assertEqual(records[1]['caption_cues'][0]['text'], 'そうね')
            self.assertEqual(records[1]['caption_cues'][0]['start'], 1.1)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(transcript_caption_at(records[1], 1.5), 'そうね')
            self.assertEqual(transcript_caption_at(records[1], 1.9), '')

    def test_plans_at_line_ends_without_splitting_speech(self):
        records = [{'end': end} for end in range(10, 601, 10)]
        self.assertEqual(plan_chunks(records, 600), [(0, 120), (120, 240), (240, 360), (360, 480), (480, 600)])

    def test_partial_failure_reuses_successful_chunks_and_marks_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'narration.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(10)
                output.writeframes(bytes(10 * 360 * 2))
            def make_records():
                return [{'id': i + 1, 'speaker': 'ずんだもん', 'caption_text': str(i),
                         'start': i * 120, 'end': (i + 1) * 120} for i in range(3)]
            def encode(args, **kwargs):
                Path(args[-1]).write_text(args[args.index('-ss') + 1])
            calls = []
            def recognize(path):
                start = float(Path(path).read_text())
                calls.append(start)
                if start == 120:
                    raise RuntimeError('Gemini HTTP 429')
                return {'words': [{'word': '音声', 'startOffset': '0.1s', 'endOffset': '1s'}],
                        'attempts': 1, 'usage': {}}
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test'}), \
                 patch('gemini_transcribe.subprocess.run', side_effect=encode), \
                 patch('gemini_transcribe.transcribe', side_effect=recognize):
                rows = make_records()
                first = transcribe_narration(audio, rows, cache_dir=root/'cache', workers=3)
                self.assertEqual(first['status'], 'partial')
                self.assertEqual(first['fallback_line_ids'], [2])
                self.assertEqual(rows[1]['caption_source'], 'script_fallback')
                calls.clear()
                second = transcribe_narration(audio, make_records(), cache_dir=root/'cache', workers=3)
                self.assertEqual(calls, [120])
                self.assertEqual(sum(c['reused'] for c in second['chunks']), 2)

    def test_429_retries_and_auth_error_does_not(self):
        payload = {'candidates': [{'content': {'parts': [{'audioTranscription': {'words': [
            {'word': 'テスト', 'startOffset': '0s', 'endOffset': '1s'}]}}]}}]}
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'audio.mp3'
            audio.write_bytes(b'audio')
            busy = HTTPError('https://example.com', 429, 'busy', {}, None)
            denied = HTTPError('https://example.com', 401, 'denied', {}, None)
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test'}), \
                 patch('gemini_transcribe.time.sleep'), \
                 patch('gemini_transcribe.urlopen', side_effect=[busy, BytesIO(json.dumps(payload).encode())]) as send:
                result = transcribe(audio)
                self.assertEqual(result['attempts'], 2)
                self.assertEqual(send.call_count, 2)
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test'}), \
                 patch('gemini_transcribe.urlopen', side_effect=denied) as send:
                with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                    transcribe(audio)
                self.assertEqual(send.call_count, 1)

    def test_chunks_run_concurrently(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'audio.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(10)
                output.writeframes(bytes(10 * 360 * 2))
            records = [{'id': i+1, 'caption_text': '台詞', 'start': i*120, 'end': (i+1)*120}
                       for i in range(3)]
            barrier = threading.Barrier(3)
            def recognize(path):
                barrier.wait(timeout=2)
                return {'words': [{'word': '音声', 'startOffset': '0.1s', 'endOffset': '1s'}],
                        'attempts': 1, 'usage': {}}
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test'}), \
                 patch('gemini_transcribe.subprocess.run'), \
                 patch('gemini_transcribe.transcribe', side_effect=recognize):
                result = transcribe_narration(audio, records, workers=3)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(len(result['chunks']), 3)

    def test_real_ffmpeg_audio_is_sent_and_word_times_are_used(self):
        payload = {'candidates': [{'content': {'parts': [{'audioTranscription': {'words': [
            {'word': 'こんにちは', 'startOffset': '0.1s', 'endOffset': '0.8s'}]}}]}}]}
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'audio.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(bytes(16000 * 2 * 2))
            records = [{'id': 1, 'caption_text': '台本', 'start': 0, 'end': 2}]
            def respond(request, timeout):
                data = json.loads(request.data)
                self.assertTrue(data['generationConfig']['audioTranscriptionConfig']['wordTimestamp'])
                self.assertGreater(len(data['contents'][0]['parts'][0]['inlineData']['data']), 100)
                return BytesIO(json.dumps(payload).encode())
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test'}), \
                 patch('gemini_transcribe.urlopen', side_effect=respond):
                result = transcribe_narration(audio, records)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(records[0]['caption_cues'][0],
                             {'text': 'こんにちは', 'start': .1, 'end': .8})


if __name__ == '__main__':
    unittest.main()
