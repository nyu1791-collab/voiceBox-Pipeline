from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import subprocess
import sys
import threading

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import generate_script, normalize_script, source_id
from tools.prepare_project import prepare
from media_editing import make_thumbnail, mux_audio, sound_effects


@pytest.fixture
def source_site(tmp_path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    site = tmp_path / 'site'
    site.mkdir()
    paragraph = 'A CPU processes instructions and a voice engine creates a waveform from text. The audio duration determines when a subtitle appears. Sources are saved with their retrieval time and are kept separate from narration. '
    (site / 'article.html').write_text('<html><head><title>CPU voice article</title></head><body style="margin:0;background:#182820">'
        '<nav style="height:80px;color:white">navigation</nav>'
        '<aside><article style="background:red">An unrelated related-article card</article></aside>'
        '<main style="background:#fff4d0;margin:20px;padding:20px">'
        '<h1>CPU voice article</h1>' + ''.join('<p>'+paragraph+'</p>' for _ in range(8)) + '</main></body></html>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(site)))
    address = f'http://127.0.0.1:{server.server_port}'
    (site / 'feed.xml').write_text(f'<rss version="2.0"><channel><title>Test feed</title><link>{address}</link><description>fixture</description>'
        f'<item><title>CPU voice article</title><link>{address}/article.html</link><pubDate>Tue, 20 Oct 2026 08:00:00 GMT</pubDate></item></channel></rss>')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield address
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_rss_article_json_and_real_browser_capture(source_site, tmp_path):
    url = source_site + '/article.html'
    script = [{'speaker': 'zundamon', 'emotion': 'surprised', 'text': '音声から時間を決めるのだ。', 'source_id': source_id(url)},
              {'speaker': 'metan', 'emotion': 'normal', 'text': '実際の記事画面も資料として使うわ。', 'source_id': source_id(url)}]
    output = tmp_path / 'prepared'
    result = prepare({'topic': 'CPU音声', 'feeds': [source_site+'/feed.xml'], 'news': False, 'search': False},
                     output, script, executable_path=shutil.which('chromium'))
    assert result['status'] == 'PROJECT_READY' and result['screenshots'] == 1
    project = json.loads((output / 'project.json').read_text())
    assert project['lines'][0]['speaker'] == 'ずんだもん'
    assert project['lines'][0]['emotion'] == 'SURPRISED'
    assert project['lines'][0]['text'] == script[0]['text']
    source = json.loads((output / 'research.json').read_text())['sources'][0]
    assert source['extraction_status'] == 'EXTRACTED' and 'waveform' in source['text']
    image = output / project['visuals'][0]['file']
    with Image.open(image) as picture:
        assert 100 < picture.height <= 700 and picture.width > 700
        assert picture.convert('RGB').getpixel((10, 10)) == (255, 244, 208)
    with pytest.raises(FileExistsError):
        prepare({'topic': 'reuse'}, output)


def test_inaccessible_source_never_becomes_a_visual(source_site, tmp_path):
    output = tmp_path / 'missing'
    with pytest.raises(ValueError, match='No extracted'):
        prepare({'topic': 'missing', 'urls': [source_site+'/missing.html'], 'news': False, 'search': False}, output)
    source = json.loads((output / 'research.json').read_text())['sources'][0]
    assert source['extraction_status'] == 'FAILED'
    assert not (output / 'project.json').exists()


@pytest.mark.parametrize('text, succeeds', [('waveform', True), ('missing-article-claim', False)])
def test_explicit_paragraph_is_used_or_reported_missing(source_site, tmp_path, text, succeeds):
    url = source_site + '/article.html'
    sid = source_id(url)
    request = {'topic': 'CPU音声', 'urls': [url], 'news': False, 'search': False,
               'capture_overrides': {sid: {'heading': 'CPU voice article',
                   'crops': [{'name': 'claim', 'text': text}]}}}
    script = [{'speaker': 'zundamon', 'text': '音声を作るのだ。', 'source_id': sid},
              {'speaker': 'metan', 'text': '波形に変換するわ。', 'source_id': sid}]
    output = tmp_path/'paragraph'
    if succeeds:
        prepare(request, output, script, executable_path=shutil.which('chromium'))
        project = json.loads((output/'project.json').read_text())
        assert project['visuals'][0]['file'].endswith('_claim.png')
    else:
        with pytest.raises(RuntimeError, match='capture failed'):
            prepare(request, output, script, executable_path=shutil.which('chromium'))
        assert json.loads((output/'preparation.json').read_text())['status'] == 'FAILED'
        assert not (output/'project.json').exists()


def test_api_is_explicit_and_unknown_source_is_rejected(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='--allow-api'):
        generate_script({}, 'gemini', 'explicit-model')
    with pytest.raises(ValueError, match='credential'):
        generate_script({}, 'gemini', 'explicit-model', allow_api=True)
    with pytest.raises(ValueError, match='source_id'):
        normalize_script([{'speaker': 'zundamon', 'text': 'unknown', 'source_id': 'invented'}],
                         {'topic': 'test', 'sources': []})


def test_research_only_never_claims_a_project_or_calls_a_model(source_site, tmp_path):
    result = prepare({'topic': 'CPU音声', 'urls': [source_site+'/article.html'], 'news': False,
                      'search': False, 'provider': 'none'}, tmp_path / 'research-only')
    assert result['status'] == 'RESEARCH_READY' and result['script_generated'] is False
    assert not (tmp_path / 'research-only/project.json').exists()


def test_se_uses_actual_timing_and_thumbnail_uses_real_frame(tmp_path):
    silent, narration, effect = (tmp_path / n for n in ('silent.mp4', 'narration.wav', 'effect.wav'))
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=320x180:r=30:d=2',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(silent)], check=True)
    for destination, source, duration in [(narration, 'anullsrc=r=48000:cl=mono', 2),
                                          (effect, 'sine=frequency=3000:sample_rate=48000', .2)]:
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', source, '-t', str(duration),
                        '-c:a', 'pcm_s16le', str(destination)], check=True)
    records = [{'start': 0, 'end': .6}, {'start': .6, 'end': 2}]
    project = {'lines': [{'visual': 'a'}, {'visual': 'a', 'se': {'file': str(effect), 'duration': .2, 'gain_db': -6}}]}
    cues = sound_effects(project, records)
    assert cues[0]['start'] == .6
    output = tmp_path / 'video.mp4'
    mux_audio(silent, narration, output, {'effects': cues})
    data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:a', '-f', 'f32le', '-'])
    samples = np.frombuffer(data, dtype='<f4')
    assert np.max(np.abs(samples[:int(.5*48000)])) < .001
    assert np.sqrt(np.mean(samples[int(.65*48000):int(.75*48000)]**2)) > .02
    assert np.max(np.abs(samples[int(.95*48000):int(1.8*48000)])) < .001
    # Exercise BGM, SE and loudnorm together; keep the finished video bitstream unchanged.
    mixed = tmp_path / 'normalized.mp4'
    mux_audio(silent, narration, mixed, {'bgm': str(effect), 'effects': cues, 'normalize': True})
    def video_hash(path):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
            '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).strip()
    assert video_hash(output) == video_hash(silent)
    assert video_hash(mixed) == video_hash(silent)
    font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    thumbnail = tmp_path / 'thumbnail.png'
    make_thumbnail(output, thumbnail, 'CPU voice and editing', font)
    with Image.open(thumbnail) as image:
        assert image.size == (1280, 720)
        assert image.getpixel((10, 710))[1] > 100
