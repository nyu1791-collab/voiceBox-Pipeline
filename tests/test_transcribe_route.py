import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from gemini_transcribe import transcribe_narration


class TranscriptionRouteTest(unittest.TestCase):
    def test_words_are_bound_to_existing_speakers(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'narration.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(bytes(8000 * 4))
            records = [{'speaker': 'ずんだもん', 'start': 0, 'end': 1},
                       {'speaker': '四国めたん', 'start': 1, 'end': 2}]
            words = [{'word': 'こんにちは', 'startOffset': '0.1s', 'endOffset': '0.8s'},
                     {'word': 'そうね', 'startOffset': '1.1s', 'endOffset': '1.8s'}]
            with patch('gemini_transcribe.subprocess.run'), patch('gemini_transcribe.transcribe', return_value=words):
                transcribe_narration(audio, records)
            self.assertEqual(records[0]['caption_cues'][0]['text'], 'こんにちは')
            self.assertEqual(records[1]['caption_cues'][0]['text'], 'そうね')
            self.assertEqual(records[1]['caption_cues'][0]['start'], 1.1)


if __name__ == '__main__':
    unittest.main()
