import unittest
from unittest.mock import patch

import gemini_workflow


class FirecrawlIntegrationTest(unittest.TestCase):
    @patch('firecrawl_sources.scrape')
    @patch('gemini_workflow.call_gemini')
    def test_research_passes_extracted_text_to_dialogue_prompt(self, gemini, scrape):
        from research import script_prompt
        gemini.return_value = ({'sources': [{'url': 'https://example.com/article',
                                              'title': 'Title', 'summary': 'summary'}]}, {})
        scrape.return_value = {'text': '本文に書かれた根拠', 'final_url': 'https://example.com/article',
                               'body_sha256': 'digest'}
        pack = gemini_workflow.research_with_gemini({'topic': '月'})
        self.assertEqual(pack['sources'][0]['firecrawl_status'], 'EXTRACTED')
        self.assertIn('本文に書かれた根拠', script_prompt(pack)['user'])

    @patch('firecrawl_sources.scrape', side_effect=RuntimeError('sensitive detail'))
    @patch('gemini_workflow.call_gemini')
    def test_failure_preserves_summary_without_error_message(self, gemini, scrape):
        gemini.return_value = ({'sources': [{'url': 'https://example.com/article',
                                              'title': 'Title', 'summary': 'summary'}]}, {})
        source = gemini_workflow.research_with_gemini({'topic': '月'})['sources'][0]
        self.assertEqual(source['firecrawl_status'], 'FAILED')
        self.assertEqual(source['extraction_status'], 'GEMINI_SUMMARY')
        self.assertEqual(source['text'], 'summary')
        self.assertNotIn('sensitive detail', str(source))


if __name__ == '__main__':
    unittest.main()
