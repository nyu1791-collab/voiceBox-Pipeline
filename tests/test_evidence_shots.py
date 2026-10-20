import copy
import pytest
from renderer.evidence_shots import compile_shots, shot_at
from media_editing import sound_effects


def example():
    project={'lines':[{'id':i,'visual':'a' if i<3 else 'b','section':'body',
                      'speaker':'ずんだもん' if i==2 else '四国めたん'} for i in range(1,5)],
             'visuals':[{'id':v,'source_url':'https://official.test/article'} for v in ('a','b')],
             'evidence_shots':[{'visual':'a','start_line_id':1},{'visual':'b','start_line_id':3}]}
    records=[{'id':1,'start':0,'end':5},{'id':2,'start':5,'end':9},
             {'id':3,'start':9,'end':10},{'id':4,'start':10,'end':19}]
    return project,records


def test_screenshot_holds_across_short_questions_and_partial_render_boundaries():
    p,r=example();t=compile_shots(p,r)
    assert shot_at(t,5.5)['visual']=='a'
    assert shot_at(t,10.5)['visual']=='b'
    assert [x['visible_seconds'] for x in t]==[9,10]
    assert shot_at(t,19) is None


def test_hidden_agenda_is_not_screenshot_reading_time():
    p,r=example()
    with pytest.raises(ValueError,match='continuous visible time'):
        compile_shots(p,r,[(0,2)])


def test_too_short_shot_and_cross_article_hold_are_rejected():
    p,r=example();p['evidence_shots'][1]['start_line_id']=2
    with pytest.raises(ValueError,match='8s'):
        compile_shots(p,r)
    p,r=example();p['visuals'][1]['source_url']='https://other.test/article'
    p['evidence_shots'][1]['start_line_id']=4
    with pytest.raises(ValueError,match='different article'):
        compile_shots(p,r)


@pytest.mark.parametrize('fraction',[True,-.1,1,float('nan')])
def test_fractional_anchor_must_remain_inside_measured_line(fraction):
    p,r=example();p['evidence_shots'][1]['start_fraction']=fraction
    with pytest.raises(ValueError,match='start_fraction'):
        compile_shots(p,r)


def test_transition_sound_matches_actual_fractional_screenshot_switch():
    p,r=example();p['evidence_shots'][1]['start_fraction']=.5
    p['audio']={'transition_se':{'file':'change.wav','duration':.4},'transition_min_gap':5}
    assert sound_effects(p,r)==[{'file':'change.wav','duration':.4,'start':9.5}]


def test_captured_evidence_rejects_changed_pixels(tmp_path):
    import json,hashlib
    from PIL import Image
    from pipeline import load_project
    image=tmp_path/'source.png';Image.new('RGB',(50,20),'white').save(image)
    project={'title':'source','lines':[{'id':1,'speaker':'ずんだもん','text':'資料なのだ。'}],
        'visuals':[{'id':'a','kind':'evidence_screenshot','file':'source.png',
        'source_url':'https://official.test/article','captured_utc':'2026-10-20T00:00:00Z',
        'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}
    path=tmp_path/'project.json';path.write_text(json.dumps(project))
    load_project(path)
    Image.new('RGB',(50,20),'black').save(image)
    with pytest.raises(ValueError,match='SHA-256 mismatch'):
        load_project(path)
