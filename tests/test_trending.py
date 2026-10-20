"""Primary-source date boundaries and evidence-before-generation integration."""
from datetime import date
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import prepare_trending as trending


def test_official_sources_reject_spoofs_redirects_old_and_undated(monkeypatch):
    urls = ['https://official.test/'+p for p in ('new', 'old', 'future', 'undated', 'redirect')]
    pack = {'sources': [{'url': 'https://summary.test/news',
        'links': urls + ['https://official.test.evil.test/news', 'https://evil.test/official.test']} ]}
    dates = ['2026-10-20', '2026-10-13', '2026-10-21', None, '2026-10-20']
    def extract(row):
        index = urls.index(row['url'])
        return {**row, 'extraction_status': 'EXTRACTED', 'article_published': dates[index],
                'body_sha256': str(index), 'final_url': 'https://other.test/news' if index == 4 else row['url']}
    monkeypatch.setattr(trending, 'extract_source', extract)
    accepted, rejected = trending.select_official(pack, ['official.test'], date(2026,10,14), date(2026,10,20))
    assert [s['url'] for s in accepted] == [urls[0]]
    assert {r['rejection_reason'] for r in rejected} == {
        'OUTSIDE_DATE_WINDOW', 'PUBLICATION_DATE_UNAVAILABLE', 'REDIRECT_OUTSIDE_OFFICIAL_HOSTS'}


def test_discovered_bodies_and_failures_are_not_fetched_again(monkeypatch):
    rows = [{'id': 's1', 'url': 'https://official.test/new', 'extraction_status': 'EXTRACTED',
             'article_published': '2026-10-20', 'body_sha256': 'abc'},
            {'id': 's2', 'url': 'https://official.test/blocked', 'extraction_status': 'FAILED'}]
    def no_fetch(_):
        pytest.fail('discovery results must be reused without retry')
    monkeypatch.setattr(trending, 'extract_source', no_fetch)
    accepted, rejected = trending.select_official({'sources': rows}, ['official.test'],
                                                   date(2026,10,14), date(2026,10,20))
    assert accepted[0]['id'] == 's1'
    assert rejected[0]['rejection_reason'] == 'BODY_UNAVAILABLE'


@pytest.mark.parametrize('capture_success', [True, False])
def test_only_captured_paragraphs_reach_one_model_call(tmp_path, monkeypatch, capture_success):
    from PIL import Image
    row = {'id': 'src-test', 'url': 'https://official.test/news', 'title': 'Official news',
           'text': 'A verified official paragraph about a new feature. '*3,
           'extraction_status': 'EXTRACTED', 'article_published': '2026-10-20', 'language': 'en'}
    monkeypatch.setattr(trending, 'research', lambda _: {'topic': 'news', 'sources': []})
    monkeypatch.setattr(trending, 'select_official', lambda *a: ([row], []))
    events = []
    async def capture(targets, output, **kwargs):
        events.append('capture')
        output.mkdir()
        Image.new('RGB', (400, 100)).save(output/'proof.png')
        return [{'id': row['id'], 'captured_utc': '2026-10-20T00:00:00Z',
                 'crops': [{'name': 'paragraph-1', 'found': capture_success,
                            'file': 'proof.png', 'sha256': 'test-hash'}]}]
    def generate(prompt, *args, **kwargs):
        events.append('generate')
        data = json.loads(prompt['user'])
        assert data['sources'][0]['text'] == row['text'].strip()
        eid = data['evidence'][0]['id']
        return {'title': 'news', 'translations': {eid: '新機能についての公式の説明です。'},
                'lines': [{'id': i, 'speaker': speaker, 'text': text, 'source_id': row['id'],
                           'evidence_id': eid} for i, speaker, text in [
                               (1,'ずんだもん','新機能なのだ。'), (2,'四国めたん','公式の説明よ。')]]}
    monkeypatch.setattr(trending, 'capture_targets', capture)
    monkeypatch.setattr(trending, 'generate_script', generate)
    request = {'topic': 'news', 'official_hosts': ['official.test'], 'provider': 'opencode',
               'model': 'deepseek-v4.1-flash-free', 'as_of': '2026-10-20'}
    output = tmp_path/'prepared'
    if capture_success:
        result = trending.prepare_trending(request, output, allow_api=True)
        assert result['status'] == 'PROJECT_READY'
        project = json.loads((output/'project.json').read_text())
        assert project['visuals'][0]['translation'] == '新機能についての公式の説明です。'
        assert events == ['capture', 'generate']
    else:
        with pytest.raises(ValueError, match='model not called'):
            trending.prepare_trending(request, output, allow_api=True)
        assert events == ['capture']
        assert json.loads((output/'preparation.json').read_text())['status'] == 'FAILED'
    timing = json.loads((output/'timing.json').read_text())
    assert timing['status'] == ('PROJECT_READY' if capture_success else 'FAILED')
    stages = [s['stage'] for s in timing['stages']]
    assert stages[:3] == ['research', 'official_sources_and_dates', 'browser_capture']
    assert ('script_generation_including_cli' in stages) == capture_success
    assert timing['total_seconds'] >= sum(s['seconds'] for s in timing['stages']) - 0.00001


def test_discovery_keeps_later_queries_in_the_source_budget(monkeypatch):
    import ddgs
    import research
    class Search:
        def __init__(self, **kwargs):
            pass
        def text(self, query, **kwargs):
            return [{'href': f'https://news.test/{query}/{i}', 'title': str(i), 'body': ''}
                    for i in range(6)]
    monkeypatch.setattr(ddgs, 'DDGS', Search)
    sources, errors = research.collect_sources({'topic': 'news', 'queries': ['first', 'second'],
                                                'news': False, 'max_sources': 2})
    assert [s['query'] for s in sources] == ['first', 'second']
    assert not errors


@pytest.mark.parametrize('eid,translations', [('invented', {}), ('s:p', {}), ('s:p', {'s:p': ''})])
def test_generated_line_requires_matching_capture_and_translation(eid, translations):
    pack = {'topic': 'news', 'sources': [{'id': 's', 'extraction_status': 'EXTRACTED'}]}
    script = {'title': 'news', 'translations': translations, 'lines': [
        {'id': 1, 'speaker': 'ずんだもん', 'text': '説明。', 'source_id': 's', 'evidence_id': eid},
        {'id': 2, 'speaker': '四国めたん', 'text': '説明。', 'source_id': 's', 'evidence_id': eid}]}
    with pytest.raises(ValueError, match='captured evidence|translation'):
        trending.bind_evidence(script, pack, [{'id': 's:p', 'source_id': 's'}])
