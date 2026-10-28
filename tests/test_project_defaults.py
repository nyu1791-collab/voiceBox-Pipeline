import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import load_project


class ProjectDefaultsTest(unittest.TestCase):
    def test_unavailable_optional_fields_use_working_defaults(self):
        project = {'title': 'テスト', 'lines': [{'id': 1, 'speaker': 'ずんだもん',
            'text': 'こんにちは', 'speech_text': '', 'emotion': 'UNKNOWN', 'visual': 'missing'}],
            'visuals': [{'id': 'available', 'file': 'image.png'}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'project.json'
            path.write_text(json.dumps(project, ensure_ascii=False))
            with patch('pipeline.prepare_visual', side_effect=lambda visual, *args: dict(visual)):
                row = load_project(path)['lines'][0]
        self.assertEqual(row['emotion'], 'NORMAL')
        self.assertNotIn('speech_text', row)
        self.assertEqual(row['visual'], 'available')
        self.assertTrue(row['visual_context_only'])


if __name__ == '__main__':
    unittest.main()
