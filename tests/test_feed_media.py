import json
import tempfile
import unittest
from pathlib import Path

from tools import feed_media


class FakeResponse:
    def __init__(self, status=200, content_type='image/png', body=b'\x89PNG' * 10, length=None):
        self.status_code = status
        self.headers = {'content-type': content_type}
        if length is not None:
            self.headers['content-length'] = str(length)
        self._body = body

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


ARTICLE = 'https://example.com/a'
JOB = {'id': 'job1', 'topic': 'テーマ', 'urls': [ARTICLE]}
ITEMS = [{'url': ARTICLE, 'media': [{'url': 'https://example.com/p.png', 'kind': 'image'},
                                    {'url': 'https://example.com/v.mp4', 'kind': 'video'}]},
         {'url': 'https://example.com/other', 'media': [{'url': 'https://example.com/x.png', 'kind': 'image'}]}]


class FetchTests(unittest.TestCase):
    def test_only_the_job_sources_are_downloaded_and_recorded(self):
        session = FakeSession({'https://example.com/p.png': FakeResponse(),
                               'https://example.com/v.mp4': FakeResponse(content_type='video/mp4', body=b'MP4' * 9)})
        with tempfile.TemporaryDirectory() as tmp:
            manifest = feed_media.fetch(JOB, ITEMS, Path(tmp) / 'media', session=session)
            self.assertEqual(session.calls, ['https://example.com/p.png', 'https://example.com/v.mp4'])
            self.assertEqual([f['kind'] for f in manifest['files']], ['image', 'video'])
            self.assertTrue((Path(tmp) / 'media' / 'manifest.json').exists())
            self.assertEqual(len(manifest['files'][0]['sha256']), 64)

    def test_every_file_carries_its_source_line(self):
        session = FakeSession({'https://example.com/p.png': FakeResponse(),
                               'https://example.com/v.mp4': FakeResponse(content_type='video/mp4')})
        items = [dict(ITEMS[0], source='Example News')]
        with tempfile.TemporaryDirectory() as tmp:
            manifest = feed_media.fetch(JOB, items, Path(tmp) / 'media', session=session)
        self.assertTrue(manifest['files'])
        for entry in manifest['files']:
            self.assertEqual(entry['credit'], '出典: Example News（https://example.com/a）')

    def test_wrong_type_size_and_status_are_skipped(self):
        items = [{'url': ARTICLE, 'media': [
            {'url': 'https://example.com/html', 'kind': 'image'},
            {'url': 'https://example.com/404.png', 'kind': 'image'},
            {'url': 'https://example.com/big.png', 'kind': 'image'},
            {'url': 'http://example.com/plain.png', 'kind': 'image'}]}]
        session = FakeSession({
            'https://example.com/html': FakeResponse(content_type='text/html'),
            'https://example.com/404.png': FakeResponse(status=404),
            'https://example.com/big.png': FakeResponse(length=feed_media.MAX_BYTES + 1)})
        with tempfile.TemporaryDirectory() as tmp:
            manifest = feed_media.fetch(JOB, items, Path(tmp) / 'media', session=session)
        self.assertEqual(manifest['files'], [])
        reasons = {s['url']: s['reason'] for s in manifest['skipped']}
        self.assertEqual(reasons['https://example.com/html'], 'type text/html')
        self.assertEqual(reasons['https://example.com/404.png'], 'HTTP 404')
        self.assertEqual(reasons['https://example.com/big.png'], 'too large')
        self.assertEqual(reasons['http://example.com/plain.png'], 'not https')

    def test_a_body_without_length_header_is_still_capped(self):
        big = b'x' * (feed_media.MAX_BYTES + 10)
        session = FakeSession({'https://example.com/p.png': FakeResponse(body=big),
                               'https://example.com/v.mp4': FakeResponse(content_type='video/mp4')})
        with tempfile.TemporaryDirectory() as tmp:
            manifest = feed_media.fetch(JOB, ITEMS[:1], Path(tmp) / 'media', session=session)
        self.assertEqual([f['kind'] for f in manifest['files']], ['video'])
        self.assertEqual(manifest['skipped'], [{'url': 'https://example.com/p.png', 'reason': 'too large'}])

    def test_network_errors_are_recorded_not_raised(self):
        import requests
        session = FakeSession({'https://example.com/p.png': requests.ConnectionError('down'),
                               'https://example.com/v.mp4': FakeResponse(content_type='video/mp4')})
        with tempfile.TemporaryDirectory() as tmp:
            manifest = feed_media.fetch(JOB, ITEMS[:1], Path(tmp) / 'media', session=session)
        self.assertEqual([s['reason'] for s in manifest['skipped']], ['ConnectionError'])
        self.assertEqual(len(manifest['files']), 1)


class DiscardTests(unittest.TestCase):
    def test_discard_deletes_the_media_and_keeps_a_receipt(self):
        session = FakeSession({'https://example.com/p.png': FakeResponse(),
                               'https://example.com/v.mp4': FakeResponse(content_type='video/mp4')})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'media'
            manifest = feed_media.fetch(JOB, ITEMS[:1], out, session=session)
            receipt = feed_media.discard(out)
            self.assertFalse(out.exists())
            data = json.loads(receipt.read_text(encoding='utf-8'))
            self.assertEqual(data['job_id'], 'job1')
            self.assertEqual(len(data['files']), len(manifest['files']))
            self.assertIsNone(feed_media.discard(out))


if __name__ == '__main__':
    unittest.main()
