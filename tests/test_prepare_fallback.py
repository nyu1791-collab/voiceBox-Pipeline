import json
import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch

from tools.prepare_project import prepare
from research import collect_sources


class PreparationFallbackTest(unittest.TestCase):
    def test_requested_source_count_is_not_a_strict_limit(self):
        urls = ['https://example.com/' + str(i) for i in range(25)]
        with patch.dict(sys.modules, {'feedparser': types.SimpleNamespace()}):
            selected, _ = collect_sources({'topic': 'テスト', 'urls': urls, 'max_sources': 25,
                                           'news': False, 'search': False})
        self.assertEqual(len(selected), 25)

    def test_missing_captures_keep_the_dialogue_and_sources(self):
        sources = [{'id': key, 'title': key, 'url': 'https://example.com/' + key,
                    'text': 'source text', 'extraction_status': 'EXTRACTED'} for key in ('a', 'b')]
        pack = {'topic': 'テスト', 'sources': sources}
        script = {'title': 'テスト', 'lines': [
            {'id': 1, 'speaker': 'ずんだもん', 'text': '一つ目', 'source_id': 'a'},
            {'id': 2, 'speaker': '四国めたん', 'text': '二つ目', 'source_id': 'b'}]}
        request = {'topic': 'テスト', 'capture_overrides': {'a': {'crops': [
            {'name': 'claim', 'text': 'missing', 'line_ids': [1, 999]}]}}}

        async def capture(*args, **kwargs):
            return [{'id': 'a', 'captured_utc': 'now', 'crops': [
                {'name': 'article', 'found': True, 'file': 'article.png', 'sha256': 'test', 'kind': 'youtube_metadata'},
                {'name': 'claim', 'found': False}]}, {'id': 'b', 'error': 'HTTP 403'}]

        with tempfile.TemporaryDirectory() as folder, \
             patch('tools.prepare_project.research', return_value=pack), \
             patch('tools.prepare_project.capture_targets', side_effect=capture), \
             patch('tools.prepare_project.load_project'):
            output = Path(folder) / 'prepared'
            result = prepare(request, output, script)
            project = json.loads((output / 'project.json').read_text())
        self.assertEqual(result['status'], 'PROJECT_READY')
        self.assertEqual(result['capture_failures'], ['a', 'b'])
        self.assertEqual(project['lines'][0]['visual'], 'a')
        self.assertEqual(project['lines'][1]['visual'], 'a')
        self.assertTrue(project['lines'][1]['visual_context_only'])
        self.assertEqual(project['visuals'][0]['capture_kind'], 'youtube_metadata')
        self.assertEqual([s['url'] for s in project['source_references']],
                         ['https://example.com/a', 'https://example.com/b'])

        async def unavailable(*args, **kwargs):
            raise RuntimeError('browser unavailable')

        with tempfile.TemporaryDirectory() as folder, \
             patch('tools.prepare_project.research', return_value=pack), \
             patch('tools.prepare_project.capture_targets', side_effect=unavailable), \
             patch('tools.prepare_project.load_project'):
            output = Path(folder) / 'prepared'
            result = prepare(request, output, script)
            project = json.loads((output / 'project.json').read_text())
            self.assertTrue((output / project['visuals'][0]['file']).is_file())
        self.assertEqual(result['screenshots'], 0)
        self.assertEqual(project['visuals'][0]['capture_kind'], 'neutral_background')
        self.assertTrue(all(row['visual_context_only'] for row in project['lines']))

        async def blocked(*args, **kwargs):
            return [{'id': 'a', 'playback_blocked': True, 'files': [
                {'path': 'blocked.png', 'sha256': 'test'}], 'crops': []},
                {'id': 'b', 'error': 'HTTP 403'}]

        with tempfile.TemporaryDirectory() as folder, \
             patch('tools.prepare_project.research', return_value=pack), \
             patch('tools.prepare_project.capture_targets', side_effect=blocked), \
             patch('tools.prepare_project.load_project'):
            output = Path(folder) / 'prepared'
            result = prepare(request, output, script)
            project = json.loads((output / 'project.json').read_text())
        self.assertEqual(result['capture_failures'], ['a', 'b'])
        self.assertEqual(project['visuals'][0]['capture_kind'], 'page_playback_blocked')


if __name__ == '__main__':
    unittest.main()
