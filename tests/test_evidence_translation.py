from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderer.evidence_translation import TranslationCards

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def test_full_translation_is_stable_and_source_moves_above_it():
    text = '実験的な機能であり、標準ビルドとは異なります。' * 10
    cards = TranslationCards([{'id': 'a', 'translation': text}],
                             {'zones': {'evidence': [225, 92, 1055, 350]}}, FONT)
    plan = cards.plans['a']
    assert len(cards.pages['a']) == 1
    assert ''.join(plan['lines']) == text
    assert 20 <= plan['font'].size <= 24
    assert all(plan['font'].getlength(line) <= 802 for line in plan['lines'])
    assert cards.state('a', 0, 1) == cards.state('a', 100, 1) == ('a',0)
    assert cards.state('untranslated', 0, 10) is None
    assert plan['evidence_region'][3]+8 == plan['xy'][1]
    frame = Image.new('RGB', (1280, 720), 'green')
    before = frame.copy()
    cards.draw(frame, cards.state('a'))
    assert frame.crop((0, 490, 1280, 720)).tobytes() == before.crop((0, 490, 1280, 720)).tobytes()
    assert frame.crop((225,plan['xy'][1],1055,490)).tobytes() != before.crop((225,plan['xy'][1],1055,490)).tobytes()


def test_oversize_translation_requires_shorter_evidence_instead_of_fast_pages():
    with pytest.raises(ValueError, match='shorter source excerpt'):
        TranslationCards([{'id': 'a', 'translation': '日本語訳を省略せず保持する。'*100}],
                         {'zones': {'evidence': [225, 92, 1055, 350]}}, FONT)


@pytest.mark.parametrize('translation', [None, '', '   '])
def test_english_evidence_requires_translation_before_render(tmp_path, translation):
    import json
    from pipeline import load_project
    image = tmp_path / 'source.png'
    Image.new('RGB', (80, 40), 'white').save(image)
    project = {'title': '資料', 'lines': [{'id': 1, 'speaker': 'ずんだもん', 'text': '説明なのだ。'}],
               'visuals': [{'id': 'source', 'file': 'source.png', 'source_language': 'en',
                            'translation': translation}]}
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(project, ensure_ascii=False))
    with pytest.raises(ValueError, match='English evidence requires'):
        load_project(path)
