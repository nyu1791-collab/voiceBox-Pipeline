from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderer.evidence_translation import TranslationCards

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def test_long_translation_pages_preserve_every_character_and_stay_below_source():
    text = '実験的な機能であり、標準ビルドとは異なります。' * 20
    cards = TranslationCards([{'id': 'a', 'translation': text}],
                             {'zones': {'evidence': [225, 92, 1055, 350]}}, FONT)
    assert len(cards.pages['a']) > 1
    assert ''.join(line for page in cards.pages['a'] for line in page) == text
    assert all(len(page) <= 3 and all(cards.font.getlength(line) <= 802 for line in page)
               for page in cards.pages['a'])
    assert cards.state('a', 0, 10) == ('a', 0)
    assert cards.state('a', 10, 10) == ('a', len(cards.pages['a'])-1)
    assert cards.state('untranslated', 0, 10) is None
    frame = Image.new('RGB', (1280, 720), 'green')
    before = frame.copy()
    cards.draw(frame, cards.state('a', 0, 10))
    assert frame.crop((0, 0, 1280, 350)).tobytes() == before.crop((0, 0, 1280, 350)).tobytes()
    assert frame.crop((0, 490, 1280, 720)).tobytes() == before.crop((0, 490, 1280, 720)).tobytes()
    assert frame.getpixel((240, 370)) != before.getpixel((240, 370))


def test_translation_never_silently_overlaps_captions():
    with pytest.raises(ValueError, match='Leave room'):
        TranslationCards([{'id': 'a', 'translation': '日本語訳。'}],
                         {'zones': {'evidence': [225, 92, 1055, 400]}}, FONT)


@pytest.mark.parametrize('translation', [None, '', '   '])
def test_english_evidence_without_translation_can_continue(tmp_path, translation):
    import json
    from pipeline import load_project
    image = tmp_path / 'source.png'
    Image.new('RGB', (80, 40), 'white').save(image)
    project = {'title': '資料', 'lines': [{'id': 1, 'speaker': 'ずんだもん', 'text': '説明なのだ。'}],
               'visuals': [{'id': 'source', 'file': 'source.png', 'source_language': 'en',
                            'translation': translation}]}
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(project, ensure_ascii=False))
    loaded = load_project(path)
    assert loaded['visuals'][0]['translation'] == (translation if translation is not None else '')
