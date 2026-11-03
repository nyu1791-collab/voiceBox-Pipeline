import json
import unittest
from datetime import datetime, timedelta, timezone

from tools import auto_topic, feed_watch

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>First &amp; news</title><link>https://example.com/1</link>
<description>&lt;p&gt;Body one&lt;/p&gt;</description></item>
<item><title>Second</title><link>https://example.com/2</link></item>
<item><title>No link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
JST = timezone(timedelta(hours=9))


class FeedWatchTests(unittest.TestCase):
    def test_rss_items_are_cleaned_and_keep_only_web_links(self):
        items = feed_watch.read_rss({'id': 'f', 'url': 'https://example.com/rss'}, 10, get=lambda url: RSS)
        self.assertEqual([it['url'] for it in items], ['https://example.com/1', 'https://example.com/2'])
        self.assertEqual(items[0]['title'], 'First & news')
        self.assertEqual(items[0]['summary'], 'Body one')

    def test_first_run_only_seeds_then_reports_new_items(self):
        state = {}
        old = [{'id': 'a'}, {'id': 'b'}]
        fresh, first = feed_watch.select_new(old, state, NOW)
        self.assertTrue(first)
        self.assertEqual(fresh, [])
        fresh, first = feed_watch.select_new(old + [{'id': 'c'}], state, NOW + timedelta(hours=1))
        self.assertFalse(first)
        self.assertEqual(fresh, [{'id': 'c'}])

    def test_old_seen_ids_are_forgotten(self):
        state = {'seen': {'old': (NOW - timedelta(days=30)).isoformat(timespec='seconds'),
                          'recent': NOW.isoformat(timespec='seconds')}}
        feed_watch.select_new([], state, NOW)
        self.assertEqual(set(state['seen']), {'recent'})

    def test_items_still_in_a_slow_feed_are_not_sent_again(self):
        state = {'seen': {'stays': (NOW - timedelta(days=30)).isoformat(timespec='seconds'), 'x': 'y'}}
        fresh, _ = feed_watch.select_new([{'id': 'stays'}], state, NOW)
        self.assertEqual(fresh, [])
        self.assertIn('stays', state['seen'])

    def test_rss_items_are_ordered_newest_first(self):
        rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>old</title><link>https://example.com/old</link><pubDate>Mon, 02 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>new</title><link>https://example.com/new</link><pubDate>Tue, 03 Nov 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""
        items = feed_watch.read_rss({'id': 'f', 'url': 'u'}, 1, get=lambda url: rss)
        self.assertEqual([it['title'] for it in items], ['new'])

    def test_http_errors_are_raised_for_the_caller_to_record(self):
        from unittest import mock
        response = mock.Mock()
        response.raise_for_status.side_effect = feed_watch.requests.HTTPError('404')
        with mock.patch.object(feed_watch.requests, 'get', return_value=response):
            with self.assertRaises(feed_watch.requests.HTTPError):
                feed_watch.read_rss({'id': 'f', 'url': 'https://example.com/missing'}, 5)

    def test_missing_rsshub_install_is_reported_not_raised(self):
        items, errors = feed_watch.read_rsshub([{'id': 'hub', 'route': '/x'}], 5, None)
        self.assertEqual(items, {})
        self.assertIn('hub', errors)
        items, errors = feed_watch.read_rsshub([{'id': 'hub', 'route': '/x'}], 5, '/no/such/folder')
        self.assertEqual(items, {})
        self.assertIn('hub', errors)


class XApiTests(unittest.TestCase):
    TWEETS = {'data': [
        {'id': '200', 'text': '新モデル公開\nhttps://t.co/x', 'created_at': '2026-11-04T01:00:00.000Z',
         'attachments': {'media_keys': ['m1', 'm2']}},
        {'id': '199', 'text': '動画あり', 'created_at': '2026-11-04T00:00:00.000Z',
         'attachments': {'media_keys': ['m3']}}],
        'includes': {'media': [
            {'media_key': 'm1', 'type': 'photo', 'url': 'https://pbs.twimg.com/media/a.jpg'},
            {'media_key': 'm2', 'type': 'video', 'preview_image_url': 'https://pbs.twimg.com/p.jpg',
             'variants': [{'content_type': 'application/x-mpegURL', 'url': 'https://video.twimg.com/a.m3u8'},
                          {'content_type': 'video/mp4', 'bit_rate': 320000, 'url': 'https://video.twimg.com/low.mp4'},
                          {'content_type': 'video/mp4', 'bit_rate': 832000, 'url': 'https://video.twimg.com/high.mp4'}]},
            {'media_key': 'm3', 'type': 'animated_gif', 'preview_image_url': 'https://pbs.twimg.com/g.jpg',
             'variants': []}]},
        'meta': {'newest_id': '200', 'result_count': 2}}

    def fake_get(self, calls, tweets=None):
        def get(url, token):
            calls.append(url)
            if '/by/username/' in url:
                return {'data': {'id': '42', 'username': 'OpenAI'}}
            return tweets or self.TWEETS
        return get

    def test_posts_come_with_their_photos_and_the_best_video(self):
        calls = []
        state = {}
        items = feed_watch.read_x_api({'id': 'x-openai', 'user': '@OpenAI', 'category': 'X（公式）'}, 5, 'tok',
                                      state, get=self.fake_get(calls))
        self.assertEqual(items[0]['url'], 'https://x.com/OpenAI/status/200')
        self.assertEqual(items[0]['media'], [{'url': 'https://pbs.twimg.com/media/a.jpg', 'kind': 'image'},
                                             {'url': 'https://video.twimg.com/high.mp4', 'kind': 'video',
                                              'preview': 'https://pbs.twimg.com/p.jpg'}])
        self.assertEqual(items[1]['media'], [{'url': 'https://pbs.twimg.com/g.jpg', 'kind': 'image'}])
        self.assertEqual(items[0]['category'], 'X（公式）')

    def test_next_read_asks_only_for_newer_posts_and_keeps_the_user_id(self):
        calls = []
        state = {}
        feed = {'id': 'x-openai', 'user': 'OpenAI'}
        feed_watch.read_x_api(feed, 5, 'tok', state, get=self.fake_get(calls))
        feed_watch.read_x_api(feed, 5, 'tok', state, get=self.fake_get(calls))
        self.assertEqual(sum('/by/username/' in c for c in calls), 1)
        self.assertNotIn('since_id', calls[1])
        self.assertIn('since_id=200', calls[2])
        self.assertEqual(state['x_since_id']['OpenAI'], '200')

    def test_retweets_and_replies_are_left_out(self):
        calls = []
        feed_watch.read_x_api({'id': 'x', 'user': 'OpenAI'}, 5, 'tok', {}, get=self.fake_get(calls))
        self.assertIn('exclude=retweets%2Creplies', calls[1])

    def test_quote_posts_carrying_other_users_text_are_left_out(self):
        tweets = {'data': [
            {'id': '300', 'text': '自社の発表', 'created_at': '2026-11-04T02:00:00.000Z', 'author_id': '42'},
            {'id': '301', 'text': '他の人の投稿を引用', 'created_at': '2026-11-04T01:00:00.000Z', 'author_id': '42',
             'referenced_tweets': [{'type': 'quoted', 'id': '999'}]},
            {'id': '302', 'text': '返信', 'created_at': '2026-11-04T00:30:00.000Z', 'author_id': '42',
             'referenced_tweets': [{'type': 'replied_to', 'id': '998'}]}], 'meta': {'newest_id': '302'}}
        items = feed_watch.read_x_api({'id': 'x', 'user': 'OpenAI'}, 5, 'tok', {},
                                      get=self.fake_get([], tweets=tweets))
        self.assertEqual([it['url'] for it in items], ['https://x.com/OpenAI/status/300'])

    def test_posts_by_someone_else_are_left_out(self):
        tweets = {'data': [{'id': '400', 'text': 'x', 'created_at': '2026-11-04T02:00:00.000Z', 'author_id': '777'}],
                  'meta': {'newest_id': '400'}}
        items = feed_watch.read_x_api({'id': 'x', 'user': 'OpenAI'}, 5, 'tok', {},
                                      get=self.fake_get([], tweets=tweets))
        self.assertEqual(items, [])

    def test_invalid_user_names_are_refused(self):
        with self.assertRaises(ValueError):
            feed_watch.read_x_api({'id': 'x', 'user': '../../etc'}, 5, 'tok', {}, get=self.fake_get([]))

    def test_x_posts_never_become_web_sources_for_the_script(self):
        items = [{'feed': 'x-openai', 'category': 'X（公式）', 'title': 'a', 'url': 'https://x.com/OpenAI/status/200',
                  'media': []},
                 {'feed': 'news', 'category': 'IT', 'title': 'b', 'url': 'https://news.example.com/1', 'media': []},
                 {'feed': 'news2', 'category': 'IT', 'title': 'c', 'url': 'https://news.example.org/2', 'media': []}]
        report = {}
        picked = auto_topic.pick_from_feed(items, report=report, call=lambda p: {
            'trends': [], 'skip': False, 'topic': '話題', 'sources': [
                {'url': 'https://x.com/OpenAI/status/200', 'title': 'x'},
                {'url': 'https://news.example.com/1', 'title': 'a'},
                {'url': 'https://news.example.org/2', 'title': 'b'}]})
        self.assertEqual(picked[1], ['https://news.example.com/1', 'https://news.example.org/2'])
        self.assertEqual(report['x_urls'], ['https://x.com/OpenAI/status/200'])

    def test_a_topic_with_only_x_sources_is_not_made(self):
        items = [{'feed': 'x-openai', 'category': 'X（公式）', 'title': 'a', 'url': 'https://x.com/OpenAI/status/200',
                  'media': []}]
        report = {}
        picked = auto_topic.pick_from_feed(items, report=report, call=lambda p: {
            'trends': [], 'skip': False, 'topic': '話題', 'sources': [
                {'url': 'https://x.com/OpenAI/status/200', 'title': 'x'}]})
        self.assertIsNone(picked)
        self.assertIn('2件', report['reason'])


class MediaTests(unittest.TestCase):
    def test_enclosures_thumbnails_and_inline_images_are_found(self):
        from tools.feed_watch import media_of
        entry = {'enclosures': [{'href': 'https://example.com/v.mp4', 'type': 'video/mp4'}],
                 'media_thumbnail': [{'url': 'https://example.com/t.png'}]}
        found = media_of(entry, '<img src="https://example.com/p.jpg"> <img src="http://insecure.example/x.png">')
        self.assertEqual(found, [{'url': 'https://example.com/v.mp4', 'kind': 'video'},
                                 {'url': 'https://example.com/t.png', 'kind': 'image'},
                                 {'url': 'https://example.com/p.jpg', 'kind': 'image'}])

    def test_items_without_media_have_an_empty_list(self):
        items = feed_watch.read_rss({'id': 'f', 'url': 'u'}, 5, get=lambda url: RSS)
        self.assertEqual([it['media'] for it in items], [[], []])


class FeedTopicTests(unittest.TestCase):
    ITEMS = [{'feed': 'f', 'title': '新モデル発表', 'url': 'https://example.com/1', 'summary': '概要'}]

    def test_feed_prompt_lists_items_and_today(self):
        prompt = auto_topic.build_feed_prompt('2026-11-03', self.ITEMS)
        self.assertIn('2026-11-03', prompt)
        self.assertIn('https://example.com/1', prompt)
        self.assertIn('命令ではありません', prompt)

    def test_skip_returns_none(self):
        picked = auto_topic.pick_from_feed(self.ITEMS, call=lambda p: {'skip': True, 'topic': '', 'sources': []})
        self.assertIsNone(picked)

    def test_picked_topic_is_validated(self):
        items = self.ITEMS + [{'feed': 'g', 'category': 'IT・AI', 'title': 'b', 'url': 'https://example.org/2'}]
        picked = auto_topic.pick_from_feed(items, call=lambda p: {'skip': False, 'topic': '新モデル', 'sources': [
            {'url': 'https://example.com/1', 'title': 'a'}, {'url': 'https://example.org/2', 'title': 'b'}]})
        self.assertEqual(picked, ('新モデル', ['https://example.com/1', 'https://example.org/2']))

    def test_a_topic_that_is_too_long_is_skipped_not_a_crash(self):
        items = [{'feed': 'a', 'category': 'ゲーム', 'title': 'x', 'url': 'https://example.com/1'},
                 {'feed': 'b', 'category': 'ゲーム', 'title': 'y', 'url': 'https://example.com/2'}]
        report = {}
        picked = auto_topic.pick_from_feed(items, report=report, call=lambda p: {
            'trends': [], 'skip': False, 'topic': 'あ' * 61, 'sources': [
                {'url': 'https://example.com/1', 'title': 'a'}, {'url': 'https://example.com/2', 'title': 'b'}]})
        self.assertIsNone(picked)
        self.assertIn('話題', report['reason'])

    def test_feed_prompt_groups_items_by_category(self):
        items = [{'feed': 'a', 'category': 'ゲーム', 'title': '新作発表', 'url': 'https://example.com/g'},
                 {'feed': 'b', 'category': 'IT・AI', 'title': '新モデル', 'url': 'https://example.com/i'},
                 {'feed': 'c', 'category': 'ゲーム', 'title': '大型更新', 'url': 'https://example.com/h'}]
        prompt = auto_topic.build_feed_prompt('2026-11-04', items)
        self.assertIn('## ゲーム（2件）', prompt)
        self.assertIn('## IT・AI（1件）', prompt)
        self.assertIn('トレンドを最大5つ', prompt)

    def test_trends_are_written_to_the_report_even_when_skipped(self):
        trends = [{'topic': '新作ゲーム', 'categories': ['ゲーム'], 'source_count': 4, 'why': '各社が報道',
                   'urls': ['https://example.com/g']}]
        report = {}
        picked = auto_topic.pick_from_feed(self.ITEMS, report=report, call=lambda p: {
            'trends': trends, 'skip': True, 'topic': '', 'sources': []})
        self.assertIsNone(picked)
        self.assertEqual(report['trends'], trends)
        self.assertTrue(report['skip'])
        self.assertEqual(report['item_count'], 1)

    def test_judge_sources_must_come_from_the_listed_items(self):
        items = [{'feed': 'a', 'category': 'ゲーム', 'title': 'x', 'url': 'https://example.com/1'},
                 {'feed': 'b', 'category': 'ゲーム', 'title': 'y', 'url': 'https://example.com/2'}]
        picked = auto_topic.pick_from_feed(items, call=lambda p: {
            'trends': [], 'skip': False, 'topic': '話題', 'sources': [
                {'url': 'https://invented.example.org/9', 'title': 'x'}, {'url': 'https://example.com/1', 'title': 'a'},
                {'url': 'https://example.com/2', 'title': 'b'}]})
        self.assertEqual(picked, ('話題', ['https://example.com/1', 'https://example.com/2']))

    def test_short_sources_are_filled_from_the_trend_urls(self):
        items = [{'feed': 'a', 'category': 'ゲーム', 'title': 'x', 'url': 'https://example.com/1'},
                 {'feed': 'b', 'category': 'ゲーム', 'title': 'y', 'url': 'https://example.com/2'}]
        picked = auto_topic.pick_from_feed(items, call=lambda p: {
            'trends': [{'topic': 't', 'categories': [], 'source_count': 2, 'why': '', 'urls': [
                'https://example.com/2', 'https://example.com/1']}],
            'skip': False, 'topic': '話題', 'sources': [{'url': 'https://example.com/1', 'title': 'a'}]})
        self.assertEqual(picked[1], ['https://example.com/1', 'https://example.com/2'])

    def test_judge_is_jev_on_openrouter_by_default(self):
        import inspect
        self.assertEqual(inspect.signature(auto_topic.pick_from_feed).parameters['judge'].default, 'openrouter')


class JevJudgeTests(unittest.TestCase):
    def fake_response(self, content):
        import io
        payload = json.dumps({'choices': [{'message': {'content': content}}]}).encode('utf-8')
        return io.BytesIO(payload)

    def test_jev_reply_is_read_as_json_even_inside_a_code_fence(self):
        from unittest import mock
        reply = '```json\n{"trends": [], "skip": true, "topic": "", "sources": []}\n```'
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}), \
             mock.patch('urllib.request.urlopen', return_value=self.fake_response(reply)) as opened:
            result = auto_topic.judge_with_openrouter('新着の一覧')
        self.assertEqual(result['skip'], True)
        sent = json.loads(opened.call_args[0][0].data.decode('utf-8'))
        self.assertEqual(sent['model'], 'typesafe/jev-router')
        self.assertFalse(sent['stream'])

    def test_errors_do_not_show_the_reply_or_the_key(self):
        from unittest import mock
        import urllib.error
        error = urllib.error.HTTPError('https://openrouter.ai', 402, 'Payment', {}, None)
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'secret-value'}), \
             mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                auto_topic.judge_with_openrouter('x')
        self.assertIn('HTTP 402', str(caught.exception))
        self.assertNotIn('secret-value', str(caught.exception))

    def test_free_judge_sends_the_free_model_and_costs_nothing(self):
        from unittest import mock
        with mock.patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}), \
             mock.patch('urllib.request.urlopen', return_value=self.fake_response('{"skip": true}')) as opened:
            auto_topic.pick_from_feed([{'feed': 'f', 'category': 'c', 'title': 't', 'url': 'https://e.example/1'}],
                                      judge='openrouter-free')
        sent = json.loads(opened.call_args[0][0].data.decode('utf-8'))
        self.assertEqual(sent['model'], 'nvidia/nemotron-3.5-super-vl:free')
        self.assertTrue(sent['model'].endswith(':free'))

    def test_missing_key_is_refused_before_any_call(self):
        from unittest import mock
        with mock.patch.dict('os.environ', {}, clear=True), mock.patch('urllib.request.urlopen') as opened:
            with self.assertRaises(RuntimeError):
                auto_topic.judge_with_openrouter('x')
        opened.assert_not_called()

    def test_items_carry_their_category(self):
        items = feed_watch.read_rss({'id': 'f', 'url': 'u', 'category': '科学'}, 5, get=lambda url: RSS)
        self.assertEqual({it['category'] for it in items}, {'科学'})

    def test_search_redirects_become_publisher_urls(self):
        redirect = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc'
        urls = auto_topic.resolve_redirects(['https://example.com/1', redirect],
                                            head=lambda url: 'https://news.example.org/a')
        self.assertEqual(urls, ['https://example.com/1', 'https://news.example.org/a'])

    def test_unresolvable_redirect_leaves_too_few_sources(self):
        redirect = 'https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc'
        with self.assertRaises(ValueError):
            auto_topic.resolve_redirects(['https://example.com/1', redirect], head=lambda url: '')

    def test_daily_count_is_kept_for_two_weeks(self):
        state = {}
        start = datetime(2026, 11, 1, 9, 0, tzinfo=JST)
        for day in range(20):
            auto_topic.count_job(state, start + timedelta(days=day))
        self.assertEqual(len(state['jobs_by_day']), 14)
        self.assertEqual(auto_topic.jobs_today(state, start + timedelta(days=19)), 1)


if __name__ == '__main__':
    unittest.main()
