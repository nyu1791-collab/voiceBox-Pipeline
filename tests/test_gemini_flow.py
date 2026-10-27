"""Exercise the research → screenshots → Gemini dialogue path without network calls."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image

from research import source_id
from tools.prepare_project import prepare


class GeminiFlowTest(unittest.TestCase):
    def test_auth_failure_is_not_retried(self):
        from gemini_workflow import call_gemini
        error = HTTPError('https://example.test', 401, 'unauthorized', {}, None)
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fixture'}), \
             patch('gemini_workflow.urlopen', side_effect=error) as send:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                call_gemini('example', [], system='test')
        self.assertEqual(send.call_count, 1)

    def test_temporary_http_error_retries_and_succeeds(self):
        from gemini_workflow import call_gemini
        error = HTTPError('https://example.test', 429, 'busy', {}, None)
        response = {'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}}]}
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fixture'}), \
             patch('gemini_workflow.urlopen', side_effect=[error, io.BytesIO(json.dumps(response).encode())]) as send, \
             patch('gemini_workflow.time.sleep'):
            result, _ = call_gemini('example', [], system='test')
        self.assertTrue(result['ok'])
        self.assertEqual(send.call_count, 2)

    def test_missing_images_and_changed_duration_can_continue(self):
        url = 'https://example.com/article'
        source = {'id': source_id(url), 'url': url, 'title': '記事', 'text': '要約',
                  'extraction_status': 'GEMINI_SUMMARY', 'is_youtube': False}
        pack = {'topic': '調査', 'sources': [source]}
        script = {'title': '会話', 'lines': [
            {'speaker': 'ずんだもん', 'text': '説明なのだ。', 'source_id': source['id']}]}

        async def failed_capture(*args, **kwargs):
            raise OSError('browser unavailable')

        def no_images(*args, **kwargs):
            return {'images': [], 'errors': [{'error': 'unavailable'}]}

        with tempfile.TemporaryDirectory() as directory, \
             patch('gemini_workflow.research_with_gemini', return_value=pack) as research, \
             patch('gemini_workflow.create_dialogue', return_value=(script, {})) as dialogue, \
             patch('tools.prepare_project.capture_targets', side_effect=failed_capture), \
             patch('tools.prepare_project.collect_related_images', side_effect=no_images):
            output = Path(directory) / 'out'
            request = {'topic': '調査', 'provider': 'gemini', 'model': 'gemini-3.8-flash', 'urls': [url], 'minutes': 5}
            result = prepare(request, output)
            project = json.loads((output / 'project.json').read_text())
            self.assertEqual(result['screenshots'], 0)
            self.assertEqual(project['lines'][0]['visual'], 'neutral-background')
            self.assertTrue(project['lines'][0]['visual_context_only'])
            self.assertTrue((output / 'materials/neutral-background.png').is_file())
            from pipeline import load_project
            loaded = load_project(output / 'project.json')
            self.assertEqual(loaded['lines'][0]['visual'], 'neutral-background')
            prepare({**request, 'minutes': 10, 'model': ''}, output, regenerate_dialogue=True)
            self.assertEqual(research.call_count, 1)
            self.assertEqual(dialogue.call_count, 2)

    def test_research_capture_then_dialogue_with_partial_capture(self):
        video = 'https://www.youtube.com/watch?v=example'
        article = 'https://example.com/article'
        missing = 'https://example.com/missing'
        requests = []
        discovery = {'sources': [{'url': url, 'title': title, 'summary': '資料の要約'}
                                 for url, title in [(video, '動画'), (article, '記事'), (missing, '欠損')]]}
        dialogue = {'title': '会話', 'lines': [
            {'speaker': 'ずんだもん', 'text': '動画を見たのだ。', 'source_id': source_id(video),
             'visual_id': 'img-reference'},
            {'speaker': '四国めたん', 'text': '記事も紹介するわ。', 'source_id': source_id(article)},
            {'speaker': 'ずんだもん', 'text': '画面が撮れない記事も読むのだ。', 'source_id': source_id(missing)},
            {'speaker': '四国めたん', 'text': '次の話よ。', 'source_id': 'unknown'}]}

        def call(request, timeout):
            requests.append(json.loads(request.data))
            text = discovery if len(requests) == 1 else dialogue
            response = {'candidates': [{'content': {'parts': [{'text': json.dumps(text, ensure_ascii=False)}]}}]}
            return io.BytesIO(json.dumps(response).encode())

        async def capture(targets, outdir, **kwargs):
            self.assertEqual(len(requests), 1)
            outdir = Path(outdir)
            outdir.mkdir(parents=True, exist_ok=True)
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

        def images(topic, outdir, *, limit):
            outdir = Path(outdir)
            outdir.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (100, 70), 'blue').save(outdir/'image.png')
            return {'query': topic, 'images': [{'id': 'img-reference',
                    'file': 'materials/related/image.png', 'source_url': 'https://example.com/photo',
                    'source_credit': '関連画像の出典', 'capture_kind': 'related_image'}], 'errors': []}

        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'GEMINI_API_KEY': 'fixture'}), \
             patch('gemini_workflow.urlopen', side_effect=call), \
             patch('tools.prepare_project.capture_targets', side_effect=capture), \
             patch('tools.prepare_project.collect_related_images', side_effect=images):
            output = Path(directory) / 'out'
            request = {'topic': '調査', 'provider': 'gemini', 'urls': [video], 'minutes': 45}
            result = prepare(request, output)
            project = json.loads((output / 'project.json').read_text())
            reused = prepare(request, output)

        self.assertEqual(result['screenshots'], 2)
        self.assertEqual(result['youtube_inputs'], 1)
        self.assertEqual(result['related_images'], 1)
        self.assertEqual(project['lines'][0]['visual'], 'img-reference')
        visual = next(item for item in project['visuals'] if item['id'] == 'img-reference')
        self.assertEqual(visual['source_url'], 'https://example.com/photo')
        self.assertEqual(len(result['capture_failures']), 1)
        self.assertTrue(reused['reused'])
        self.assertEqual(len(requests), 2)
        self.assertEqual(project['lines'][2]['source_id'], source_id(missing))
        self.assertTrue(project['lines'][2]['visual_context_only'])
        self.assertTrue(project['lines'][-1]['visual_context_only'])
        self.assertEqual(requests[0]['generationConfig']['thinkingConfig']['thinkingLevel'], 'MEDIUM')
        self.assertIn({'googleSearch': {}}, requests[0]['tools'])
        self.assertIn({'urlContext': {}}, requests[1]['tools'])
        schema = requests[1]['generationConfig']['responseFormat']['text']['schema']
        lines = schema['properties']['lines']['items']
        self.assertIn('visual_id', lines['required'])
        self.assertIn('img-reference', lines['properties']['visual_id']['enum'])
        self.assertTrue(any(part.get('fileData', {}).get('fileUri') == video
                            for part in requests[1]['contents'][0]['parts']))
        self.assertEqual(sum('inlineData' in part for part in requests[1]['contents'][0]['parts']), 3)


if __name__ == '__main__':
    unittest.main()
