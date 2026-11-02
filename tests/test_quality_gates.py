import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quality_gates import image_allowed, numeric_checks


class ImageRightsGoalTest(unittest.TestCase):
    def test_third_party_video_frames_are_not_shown(self):
        allowed, _ = image_allowed({'capture_kind': 'youtube_frame',
                                    'source_url': 'https://www.youtube.com/watch?v=x'})
        self.assertFalse(allowed)
        allowed, _ = image_allowed({'capture_kind': 'page_playback_blocked',
                                    'source_url': 'https://www.youtube.com/shorts/x'})
        self.assertFalse(allowed)

    def test_related_images_come_from_public_sector_pages(self):
        public = {'capture_kind': 'related_image', 'source_url': 'https://www.city.kuki.lg.jp/a.html'}
        police = {'capture_kind': 'related_image', 'source_url': 'https://www.police.pref.okinawa.jp/x/'}
        news = {'capture_kind': 'related_image', 'source_url': 'https://news.example.com/photo'}
        self.assertTrue(image_allowed(public)[0])
        self.assertTrue(image_allowed(police)[0])
        self.assertFalse(image_allowed(news)[0])


class NumericGroundingGoalTest(unittest.TestCase):
    def test_numbers_the_script_uses_are_found_in_sources(self):
        sources = [{'title': '青切符', 'text': '反則金は5,000円。16歳以上が対象。'}]
        lines = [{'id': 1, 'text': '反則金は5000円よ。'}]
        unsupported, conflicts = numeric_checks(lines, sources)
        self.assertEqual(unsupported, [])
        self.assertEqual(conflicts, [])

    def test_unstated_numbers_are_reported_without_stopping(self):
        sources = [{'title': '青切符', 'text': '反則金は5,000円。'}]
        lines = [{'id': 7, 'text': '反則金は9,000円よ。'}]
        unsupported, _ = numeric_checks(lines, sources)
        self.assertEqual(unsupported, [{'line_id': 7, 'number': '9000'}])

    def test_conflicting_counts_across_sources_are_reported(self):
        sources = [{'title': 'a', 'text': '対象は113種類です。'},
                   {'title': 'b', 'text': '対象は110項目と言われています。'}]
        _, conflicts = numeric_checks([], sources)
        self.assertEqual(conflicts, [{'unit': '反則行為の数', 'values': ['110', '113']}])


if __name__ == '__main__':
    unittest.main()
