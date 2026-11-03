import json
import tempfile
import unittest
from pathlib import Path

from tools import post_card

POST = {'url': 'https://x.com/OpenAI/status/200', 'summary': '新モデル <script>alert(1)</script> & 詳細',
        'published': '2026-11-04T02:00:00Z', 'media': []}


class CardTests(unittest.TestCase):
    def test_post_text_is_escaped_not_run_as_html(self):
        page = post_card.build_html(POST)
        self.assertNotIn('<script>alert', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertIn('&amp; 詳細', page)

    def test_author_and_link_come_from_the_post_url(self):
        page = post_card.build_html(POST)
        self.assertIn('@OpenAI', page)
        self.assertIn('https://x.com/OpenAI/status/200', page)

    def test_card_states_the_source(self):
        page = post_card.build_html(dict(POST, source='@OpenAI（X公式）'))
        self.assertIn('出典: @OpenAI（X公式）', page)

    def test_picture_is_embedded_as_data_when_given(self):
        page = post_card.build_html(POST, picture_url='data:image/png;base64,AAAA')
        self.assertIn('src="data:image/png;base64,AAAA"', page)
        self.assertNotIn('<img', post_card.build_html(POST))

    def test_no_card_without_a_chosen_x_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(post_card.make_card({'urls': ['https://news.example/1']}, [POST], tmp))
            self.assertIsNone(post_card.make_card({'x_urls': ['https://x.com/other/status/9']}, [POST], tmp))

    def test_picture_data_url_uses_the_file_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.png'
            path.write_bytes(b'\x89PNG')
            self.assertTrue(post_card.picture_data_url(path).startswith('data:image/png;base64,'))

    def test_record_card_adds_the_card_to_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp)
            (media / 'manifest.json').write_text(json.dumps({'files': []}), encoding='utf-8')
            card = media / 'card.png'
            card.write_bytes(b'png-bytes')
            post_card.record_card(media, POST, card)
            files = json.loads((media / 'manifest.json').read_text(encoding='utf-8'))['files']
            self.assertEqual(files[0]['kind'], 'card')
            self.assertEqual(files[0]['source_url'], POST['url'])
            self.assertEqual(files[0]['bytes'], len(b'png-bytes'))


if __name__ == '__main__':
    unittest.main()
