"""Check subtitle export and video cue selection use the same times and words."""
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

from subtitle_tools import transcript_caption_at, write_subtitles


class SubtitleExportTest(unittest.TestCase):
    def test_recognized_and_fallback_cues_are_exported(self):
        class Event:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        class Document(list):
            def __init__(self):
                super().__init__()
                self.info, self.styles = {}, {}
            def save(self, *args, **kwargs):
                pass
        fake_subtitles = types.SimpleNamespace(SSAFile=Document, SSAEvent=Event,
            SSAStyle=lambda **kwargs: kwargs, Color=lambda *args: args)
        fake_renderer = types.SimpleNamespace(caption_states=lambda text, *args: [text],
            wrap_words=lambda text, *args, **kwargs: [text], _font=lambda *args: object())
        rows = [{'id': 1, 'speaker': 'ずんだもん', 'caption_text': '元の台本', 'start': 0, 'end': 1,
                 'caption_cues': [{'text': '音声認識', 'start': .1, 'end': .8}]},
                {'id': 2, 'speaker': '四国めたん', 'caption_text': '予備', 'start': 1, 'end': 2,
                 'caption_source': 'script_fallback'}]
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {
                'pysubs2': fake_subtitles, 'renderer.render_reusable_landscape_raw': fake_renderer}):
            cues = write_subtitles(rows, '/unused/font', {}, Path(folder))
            metadata = json.loads((Path(folder) / 'subtitles.json').read_text())
        self.assertEqual((cues[0]['text'], cues[0]['start_ms'], cues[0]['end_ms']), ('音声認識', 100, 800))
        self.assertEqual(transcript_caption_at(rows[0], .5), cues[0]['text'])
        self.assertEqual(metadata['fallback_line_ids'], [2])
        self.assertTrue(metadata['word_alignment'])


if __name__ == '__main__':
    unittest.main()
