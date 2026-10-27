"""Exercise the research → screenshots → Gemini dialogue path without network calls."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from research import source_id
from tools.prepare_project import prepare


class GeminiFlowTest(unittest.TestCase):
    def test_research_capture_then_dialogue_with_partial_capture(self):
        video = 'https://www.youtube.com/watch?v=example'
        article = 'https://example.com/article'
        missing = 'https://example.com/missing'
        requests = []
        discovery = {'sources': [{'url': url, 'title': title, 'summary': '資料の要約'}
                                 for url, title in [(video, '動画'), (article, '記事'), (missing, '欠損')]]}
        dialogue = {'title': '会話', 'lines': [
            {'speaker': 'ずんだもん', 'text': '動画を見たのだ。', 'source_id': source_id(video)},
            {'speaker': '四国めたん', 'text': '記事も紹介するわ。', 'source_id': source_id(article)},
            {'speaker': '四国めたん', 'text': '次の話よ。', 'source_id': 'unknown'}]}

        def call(request, timeout):
            requests.append(json.loads(request.data))
            text = discovery if len(requests) == 1 else dialogue
            response = {'candidates': [{'content': {'parts': [{'text': json.dumps(text, ensure_ascii=False)}]}}]}
            return io.BytesIO(json.dumps(response).encode())

        async def capture(targets, outdir, **kwargs):
            self.assertEqual(len(requests), 1)
            outdir = Path(outdir)
            outdir.mkdir(parents=True)
            result = []
            for target in targets:
                row = {'id': target['id'], 'url': target['url'], 'captured_utc': 'now', 'crops': [], 'files': []}
                if target['url'] == missing:
                    row['error'] = 'unavailable'
                else:
                    name = target['id'] + '.png'
                    Image.new('RGB', (100, 70), 'white').save(outdir / name)
                    row['files'] = [{'path': name, 'sha256': 'fixture'}]
                result.append(row)
            return result

        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'GEMINI_API_KEY': 'fixture'}), \
             patch('gemini_workflow.urlopen', side_effect=call), \
             patch('tools.prepare_project.capture_targets', side_effect=capture):
            output = Path(directory) / 'out'
            request = {'topic': '調査', 'provider': 'gemini', 'urls': [video], 'minutes': 45}
            result = prepare(request, output)
            project = json.loads((output / 'project.json').read_text())
            reused = prepare(request, output)

        self.assertEqual(result['screenshots'], 2)
        self.assertEqual(result['youtube_inputs'], 1)
        self.assertEqual(len(result['capture_failures']), 1)
        self.assertTrue(reused['reused'])
        self.assertEqual(len(requests), 2)
        self.assertTrue(project['lines'][-1]['visual_context_only'])
        self.assertEqual(requests[0]['generationConfig']['thinkingConfig']['thinkingLevel'], 'MEDIUM')
        self.assertIn({'googleSearch': {}}, requests[0]['tools'])
        self.assertIn({'urlContext': {}}, requests[1]['tools'])
        self.assertTrue(any(part.get('fileData', {}).get('fileUri') == video
                            for part in requests[1]['contents'][0]['parts']))
        self.assertEqual(sum('inlineData' in part for part in requests[1]['contents'][0]['parts']), 2)


if __name__ == '__main__':
    unittest.main()
