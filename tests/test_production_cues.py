"""Measured chapters, readable overview, grounded animation and restrained sound cues."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image

from renderer.production_cues import compile_direction, attach_motion, draw_direction, cache_state
from renderer.motion_diagrams import bound_timeline, render_panel, active_diagram
from media_editing import sound_effects

FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'


@pytest.fixture
def authored_project():
    project = {'title': '機能テスト', 'chapters': [
        {'id': 'chapter1', 'title': '制作時間', 'summary': '検索と台本生成にかかった時間'},
        {'id': 'chapter2', 'title': '結果', 'summary': '成果物と残った課題'}],
        'visuals': [{'id': 'proof', 'file': 'proof.png', 'source_id': 'measurements',
                     'source_url': 'measurement:20261020', 'source_credit': '制作時間の実測',
                     'evidence_text': '処理時間 検索と本文取得 6.53秒。処理時間 DeepSeek 65.69秒。'}],
        'lines': [
            {'id': 1, 'speaker': '四国めたん', 'section': 'intro', 'display': 'overview', 'text': '今回は制作時間と結果を紹介するわ。'},
            {'id': 2, 'speaker': 'ずんだもん', 'section': 'chapter1', 'text': '何に時間がかかったのだ？'},
            {'id': 3, 'speaker': '四国めたん', 'section': 'chapter1', 'text': '検索・本文取得は6.53秒だったわ。'},
            {'id': 4, 'speaker': '四国めたん', 'section': 'chapter1', 'text': 'DeepSeekは65.69秒だったわ。'},
            {'id': 5, 'speaker': 'ずんだもん', 'section': 'chapter2', 'text': '結果はどうだったのだ？'},
            {'id': 6, 'speaker': '四国めたん', 'section': 'chapter2', 'text': '台本と画像を保存できたわ。'}],
        'explainers': [{'id': 'Timing', 'scene_id': 'chapter1', 'title': '工程の処理時間',
            'template': 'bars', 'metric': '処理時間', 'elements': [
                {'id': 'search', 'line_id': 3, 'label': '検索・本文取得', 'value': 6.53,
                 'unit': '秒', 'value_quote': '処理時間 検索と本文取得 6.53秒'},
                {'id': 'script', 'line_id': 4, 'label': 'DeepSeek', 'value': 65.69,
                 'unit': '秒', 'value_quote': '処理時間 DeepSeek 65.69秒'}]}]}
    for line in project['lines']:
        line.update(visual='proof', source_id='measurements', se=None)
    return project


def measured_records(project):
    windows = [(0, 2), (2, 3), (3, 5), (5, 7), (7, 8), (8, 10)]
    return [{'id': line['id'], 'scene_id': line['section'], 'voice_text': line['text'],
             'caption_text': line['text'], 'visual_id': line['visual'], 'start': a, 'end': b}
            for line, (a, b) in zip(project['lines'], windows)]


def test_chapters_follow_measured_lines_and_keep_overview_inside_intro(authored_project):
    cues = compile_direction(authored_project, measured_records(authored_project))
    assert [(c['start'], c['end']) for c in cues['chapters']] == [(2, 7), (7, 10)]
    assert cues['overview'] == [{'start': 0, 'end': 2}]
    assert cache_state(cues, 2)[0][0] == 'chapter1'
    assert cache_state(cues, 7)[0][0] == 'chapter2'
    broken = copy.deepcopy(authored_project)
    broken['lines'][3]['section'] = 'intro'
    with pytest.raises(ValueError, match='contiguous'):
        compile_direction(broken, measured_records(broken))


def test_motion_uses_evidence_numbers_and_real_times(authored_project, tmp_path):
    rows = measured_records(authored_project)
    presentation = {}
    attach_motion(authored_project, rows, presentation, tmp_path)
    timeline = bound_timeline(presentation, rows, tmp_path)
    diagram = timeline['diagrams'][0]
    assert [(e['start'], e['end']) for e in diagram['elements']] == [(3, 5), (5, 7)]
    assert active_diagram(timeline, 3.5)['id'] == 'Timing'
    authored_project['explainers'][0]['elements'][0]['value'] = 99
    with pytest.raises(ValueError, match='数値が不一致'):
        attach_motion(authored_project, rows, {}, tmp_path)


def test_renderer_changes_pixels_and_keeps_subtitle_region_free(authored_project, tmp_path):
    if not Path(FONT).exists():
        pytest.skip('Japanese font is required for typography verification')
    rows = measured_records(authored_project)
    cues = compile_direction(authored_project, rows)
    frames = []
    for t in (0.5, 3, 4):
        frame = Image.new('RGB', (1280, 720), 'white')
        draw_direction(frame, cues, t, FONT)
        assert frame.crop((205, 498, 1075, 686)).getextrema() == ((255,255),)*3
        frames.append(frame)
    assert frames[0].tobytes() != frames[1].tobytes() != frames[2].tobytes()
    presentation = {}; attach_motion(authored_project, rows, presentation, tmp_path)
    diagram = presentation['motion_diagrams']['diagrams'][0]
    a = render_panel(diagram, 3, (830,390), FONT, 1)
    b = render_panel(diagram, 3.3, (830,390), FONT, 1)
    assert a.tobytes() != b.tobytes()


def test_chapter_sound_replaces_page_sound_and_respects_cooldown(authored_project):
    authored_project['audio'] = {'chapter_se': {'file': 'chapter.wav'},
        'transition_se': {'file': 'page.wav'}, 'transition_min_gap': 2}
    authored_project['lines'][2]['visual'] = 'new-proof'
    effects = sound_effects(authored_project, measured_records(authored_project))
    assert [(e['file'], e['start']) for e in effects] == [('chapter.wav', 2), ('page.wav', 5), ('chapter.wav', 7)]


def test_standard_project_preflight_rejects_ungrounded_chart_before_tts(authored_project, tmp_path):
    from pipeline import load_project
    Image.new('RGB', (800,400), 'white').save(tmp_path/'proof.png')
    path = tmp_path/'project.json'
    path.write_text(json.dumps(authored_project, ensure_ascii=False))
    assert load_project(path)['chapters'][0]['id'] == 'chapter1'
    authored_project['explainers'][0]['elements'][0]['value'] = 123
    path.write_text(json.dumps(authored_project, ensure_ascii=False))
    with pytest.raises(ValueError, match='数値が不一致'):
        load_project(path)
