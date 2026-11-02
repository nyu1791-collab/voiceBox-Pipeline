import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tools import auto_topic

JST = timezone(timedelta(hours=9))


class AutoTopicTests(unittest.TestCase):
    def test_valid_result_keeps_https_sources_only(self):
        topic, urls = auto_topic.valid_topic({
            'topic': ' 生成AIの新機能  ', 'sources': [
                {'url': 'https://example.com/a', 'title': 'a'},
                {'url': 'http://example.com/insecure', 'title': 'b'},
                {'url': 'https://example.com/b', 'title': 'c'},
                {'url': 'https://example.com/a', 'title': 'dup'}]})
        self.assertEqual(topic, '生成AIの新機能')
        self.assertEqual(urls, ['https://example.com/a', 'https://example.com/b'])

    def test_too_few_sources_is_refused(self):
        with self.assertRaises(ValueError):
            auto_topic.valid_topic({'topic': 'テーマ', 'sources': [{'url': 'https://example.com/a', 'title': 'a'}]})

    def test_empty_or_long_topic_is_refused(self):
        sources = [{'url': 'https://example.com/a', 'title': 'a'}, {'url': 'https://example.com/b', 'title': 'b'}]
        with self.assertRaises(ValueError):
            auto_topic.valid_topic({'topic': '  ', 'sources': sources})
        with self.assertRaises(ValueError):
            auto_topic.valid_topic({'topic': 'あ' * 61, 'sources': sources})

    def test_pick_topic_asks_gemini_once_with_today_and_validates_it(self):
        calls = []

        def fake(prompt):
            calls.append(prompt)
            return {'topic': '新しい話題', 'sources': [
                {'url': 'https://example.com/a', 'title': 'a'}, {'url': 'https://example.com/b', 'title': 'b'}]}

        topic, urls = auto_topic.pick_topic(call=fake, now=datetime(2026, 11, 3, 9, 0, tzinfo=JST))
        self.assertEqual(topic, '新しい話題')
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(calls), 1)
        self.assertIn('2026-11-03', calls[0])

    def test_write_job_matches_the_voicebox_job_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = auto_topic.write_job(Path(tmp) / 'jobs', '話題', ['https://example.com/a', 'https://example.com/b'],
                                        now=datetime(2026, 11, 3, 9, 0, tzinfo=JST))
            job = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(job['status'], 'approved')
        self.assertEqual(job['topic'], '話題')
        self.assertEqual(job['chosen_by'], 'gemini')
        self.assertEqual(len(job['id']), 12)
        self.assertEqual(job['approved_at'], '2026-11-03 09:00')


if __name__ == '__main__':
    unittest.main()
