from pathlib import Path
import json
import sys

import pysubs2
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from subtitle_tools import phrase_breaks, wrap_japanese, write_subtitles
from renderer.render_reusable_landscape_raw import caption_states

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
ROOT = Path(__file__).resolve().parents[1]


def test_phrase_layout_preserves_units_words_and_punctuation():
    text = '「予算は100万円です」。VOICEVOXは48 kHzの音声を作ります。'
    face = ImageFont.truetype(FONT, 36)
    rows = wrap_japanese(text, face, 250, strict=True)
    assert rows and ''.join(rows) == text
    assert all(face.getlength(row) <= 250 for row in rows)
    assert any('100万円' in row for row in rows)
    assert any('48 kHz' in row for row in rows)
    assert any('VOICEVOX' in row for row in rows)
    assert all(row[0] not in '、。！？」』）' and row[-1] not in '「『（' for row in rows)
    for term in ('100万円', '48 kHz', 'VOICEVOX'):
        begin = text.index(term)
        assert not set(phrase_breaks(text)).intersection(range(begin+1, begin+len(term)))


def test_export_matches_video_caption_states_and_voice_intervals(tmp_path):
    layout = json.loads((ROOT/'config/landscape_profile_v18.json').read_text())['layout']
    text = 'パスキーは、秘密鍵を使って本人であることを証明します。' * 4
    records = [{'id': 1, 'speaker': 'ずんだもん', 'caption_text': text, 'start': 0.0, 'end': 9.6},
               {'id': 2, 'speaker': '四国めたん', 'caption_text': '公開鍵と秘密鍵の役割を分けて考えましょう。', 'start': 9.6, 'end': 13.2}]
    cues = write_subtitles(records, FONT, layout, tmp_path)
    states = caption_states(text, FONT, layout)
    assert [c['text'] for c in cues if c['line_id'] == 1] == list(states)
    assert ''.join(c['text'] for c in cues) == ''.join(r['caption_text'] for r in records)
    assert cues[0]['start_ms'] == 0 and cues[-1]['end_ms'] == 13200
    assert all(a['end_ms'] == b['start_ms'] for a, b in zip(cues, cues[1:]))
    assert all(1 <= len(c['lines']) <= 3 for c in cues)
    for suffix, tolerance in [('srt', 1), ('ass', 10), ('vtt', 1)]:
        loaded = pysubs2.load(str(tmp_path/('subtitles.'+suffix)))
        assert len(loaded) == len(cues)
        for event, cue in zip(loaded, cues):
            assert abs(event.start-cue['start_ms']) <= tolerance
            assert abs(event.end-cue['end_ms']) <= tolerance
            assert event.plaintext.replace('\n', '') == cue['text']
    metadata = json.loads((tmp_path/'subtitles.json').read_text())
    assert metadata['word_alignment'] is False


def test_closing_quote_stays_with_its_sentence():
    layout = json.loads((ROOT/'config/landscape_profile_v18.json').read_text())['layout']
    text = '「パスキーを使います。」次に、仕組みを説明します。'
    states = caption_states(text, FONT, layout)
    assert ''.join(states) == text
    assert all(state[0] not in '」』）】、。' for state in states)


def test_long_unbroken_text_is_split_without_losing_text():
    layout = json.loads((ROOT/'config/landscape_profile_v18.json').read_text())['layout']
    text = 'A' * 300
    states = caption_states(text, FONT, layout)
    assert len(states) > 1
    assert ''.join(states) == text
