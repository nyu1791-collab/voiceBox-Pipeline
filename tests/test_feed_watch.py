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
        picked = auto_topic.pick_from_feed(self.ITEMS, call=lambda p: {'skip': False, 'topic': '新モデル', 'sources': [
            {'url': 'https://example.com/1', 'title': 'a'}, {'url': 'https://example.org/2', 'title': 'b'}]})
        self.assertEqual(picked, ('新モデル', ['https://example.com/1', 'https://example.org/2']))

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
