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
    assert cache_state(cues, 3.0) != cache_state(cues, 3.025)  # Same percent, different drawn pixels.
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


def test_unillustrated_intro_does_not_need_evidence_text(authored_project, tmp_path):
    authored_project['visuals'].append({'id': 'intro-card', 'file': 'intro.png',
        'source_credit': '利用者提供の導入画像', 'source_url': 'user-provided:intro'})
    authored_project['lines'][0]['visual'] = 'intro-card'
    attach_motion(authored_project, measured_records(authored_project), {}, tmp_path)


def test_first_chapter_sound_without_intro_and_mismatched_timing(authored_project):
    authored_project['lines'] = authored_project['lines'][1:]
    authored_project['audio'] = {'chapter_se': {'file': 'chapter.wav'}}
    records = [{'id': l['id'], 'start': i, 'end': i+1} for i,l in enumerate(authored_project['lines'])]
    assert sound_effects(authored_project, records)[0]['start'] == 0
    with pytest.raises(ValueError, match='must match'):
        sound_effects(authored_project, records[:-1])
    records[0]['id'] = 999
    with pytest.raises(ValueError, match='must match'):
        sound_effects(authored_project, records)


def test_production_research_request_uses_evidence_first(monkeypatch, tmp_path):
    from tools.actions_run import prepare_research_request
    seen = []
    monkeypatch.setattr('tools.prepare_project.prepare', lambda *a,**k: seen.append('standard'))
    monkeypatch.setattr('tools.prepare_trending.prepare_trending', lambda *a,**k: seen.append('evidence'))
    prepare_research_request({'mode':'evidence_first'}, tmp_path)
    assert seen == ['evidence']
    with pytest.raises(ValueError, match='Unknown'):
        prepare_research_request({'mode':'invented'}, tmp_path)


def test_overflowing_overview_is_rejected_before_synthesis(authored_project, tmp_path, monkeypatch):
    import argparse
    import pipeline
    from renderer.production_cues import validate_direction_layout
    if not Path(FONT).exists():
        pytest.skip('Japanese font required')
    # Five rows cannot hold a 42-character, three-line description at the fixed type size.
    project = {'title':'layout','chapters':[{'id':f'c{i}','title':'章','summary':'あ'*42} for i in range(5)],
        'lines':[{'id':0,'section':'intro','display':'overview'}]+[{'id':i+1,'section':f'c{i}'} for i in range(5)]}
    with pytest.raises(ValueError, match='Overview text is too long'):
        validate_direction_layout(project,FONT)
    monkeypatch.setattr(pipeline,'load_project',lambda _:project)
    monkeypatch.setattr(pipeline,'read_json',lambda _: {'font':FONT})
    monkeypatch.setattr(pipeline,'engine_fingerprint',lambda *a:pytest.fail('must stop before engine setup'))
    with pytest.raises(ValueError, match='Overview text is too long'):
        pipeline.execute(argparse.Namespace(project=tmp_path/'project.json',out=tmp_path/'out'))


def test_standard_renderer_composes_background_layers_mouth_captions_and_graphs(authored_project, tmp_path):
    """Exercise the real renderer/FFmpeg path with labelled test layers, not native character art/TTS."""
    if not Path(FONT).exists():
        pytest.skip('Japanese font required for full composition')
    import wave
    import subprocess
    import numpy as np
    import pipeline
    from PIL import ImageDraw
    from renderer import render_reusable_landscape_raw as renderer

    # Build synthetic layers in the native loader's directory structure. These are fixtures only.
    shell = tmp_path/'fixture-shell'
    for character, color in [('Zundamon','#21C366'), ('Metan','#E048A0')]:
        root = shell/character
        fixed, mouths, expressions = renderer._rs.character_layers(character)
        for name in set(fixed+mouths+sum(expressions.values(), [])):
            path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new('RGBA',(100,200))
            draw = ImageDraw.Draw(image)
            if name == fixed[0]: draw.rectangle((8,5,91,195), fill=color)
            if name in mouths:
                draw.rectangle((32,50,68,55+10*mouths.index(name)),fill=['white','blue','black'][mouths.index(name)])
            image.save(path)
    Image.new('RGB',(1280,720),'#123F30').save(tmp_path/'fixture-background.png')
    Image.new('RGB',(800,250),'#FFDD88').save(tmp_path/'proof.png')
    Image.new('RGB',(800,250),'#88DDFF').save(tmp_path/'intro.png')
    authored_project['visuals'].append({'id':'intro','file':'intro.png','source_credit':'テスト導入画像','source_url':'fixture:intro'})
    authored_project['lines'][0]['visual'] = 'intro'
    authored_project['chapters'] = authored_project['chapters'][:1]
    authored_project['lines'] = authored_project['lines'][:4]
    path = tmp_path/'project.json'; path.write_text(json.dumps(authored_project,ensure_ascii=False))
    project = pipeline.load_project(path)
    out = tmp_path/'output'; out.mkdir()
    rows = []
    for line,(a,b) in zip(project['lines'],[(0,.6),(.6,1.2),(1.2,2.3),(2.3,3.6)]):
        rows.append({'id':line['id'],'speaker':line['speaker'],'caption_text':line['text'],
            'voice_text':line['text'],'scene_id':line['section'],'visual_id':line['visual'],
            'emotion':'NORMAL','start':a,'end':b,'voicevox_speaker_id':pipeline.SPEAKERS[line['speaker']]})
    samples = np.arange(round(3.6*48000))/48000
    signal = (10000*np.sin(2*np.pi*440*samples)*(samples%.24<.12)).astype('<i2')
    with wave.open(str(out/'narration.wav'),'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000); wav.writeframes(signal.tobytes())
    pipeline.atomic_json(out/'timing.json',{'records':rows,'scope':'Synthetic test signal, no speech synthesis'})
    runtime={'shell':str(shell),'background':str(tmp_path/'fixture-background.png'),'font':FONT}
    _, probe = pipeline.render(project,runtime,out,rows)
    video = next(s for s in probe['streams'] if s['codec_type']=='video')
    assert (video['width'],video['height'],video['r_frame_rate']) == (640,360,'24/1')
    report = json.loads((out/'render-report.json').read_text())
    assert report['character_positions'] == {'ずんだもん':'LEFT','四国めたん':'RIGHT'}
    assert all(len(states)>=2 for states in report['mouth_states'].values())
    assert report['motion_diagrams']['count'] == 1
    presentation=json.loads((out/'presentation.json').read_text())
    assert presentation['sections'][1]['heading'] == '第1章 制作時間'
    frames = np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(out/'video.mp4'),
        '-vf',r'select=eq(n\,6)+eq(n\,72)','-fps_mode','passthrough','-f','rawvideo','-pix_fmt','rgb24','-']),dtype=np.uint8).reshape(-1,360,640,3)
    assert len(frames)==2
    for frame in frames:
        assert np.max(np.abs(frame[150,2].astype(int)-[18,63,48])) < 12  # Registered background survives.
        assert frame[230,50,1] > frame[230,50,0]+40  # Left fixture character.
        assert frame[230,590,0] > frame[230,590,1]+40  # Right fixture character.
        assert frame[335,320].min() > 180  # Backplate pixel clear of subtitle glyphs.
    assert np.mean(np.abs(frames[0,50:240,115:525].astype(int)-frames[1,50:240,115:525].astype(int))) > 10
    subprocess.run(['ffmpeg','-v','error','-i',str(out/'video.mp4'),'-f','null','-'],check=True)
