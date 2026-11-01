from pathlib import Path
import json
import math
import struct
import subprocess
import sys
import wave

import pysubs2
import pytest
from PIL import Image, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shorts  # noqa: E402
import render_reusable_short as rs  # noqa: E402  (patched by shorts' landscape import)

FONT = next((p for p in ['/usr/share/fonts/truetype/fonts-japanese-gothic.ttf',
                         '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'] if Path(p).exists()))


def rec(i, speaker, text, start, end, scene='main', visual='v1'):
    return {'id': i, 'speaker': speaker, 'caption_text': text, 'start': start, 'end': end,
            'scene_id': scene, 'visual_id': visual, 'emotion': 'NORMAL'}


def test_segment_prefers_section_start_and_respects_limits():
    records = [rec(1, 'ずんだもん', 'a', 0, 10), rec(2, '四国めたん', 'b', 10, 20),
               rec(3, 'ずんだもん', 'c', 20, 30, 'next'), rec(4, '四国めたん', 'd', 30, 45, 'next'),
               rec(5, 'ずんだもん', 'e', 45, 70, 'next')]
    assert shorts.select_segment(records, 15, 25) == (2, 3)
    assert shorts.select_segment(records, 15, 59, lines='2-4') == (1, 3)
    with pytest.raises(ValueError, match='limit'):
        shorts.select_segment(records, 15, 30, lines='1-5')
    with pytest.raises(ValueError, match='not in timing'):
        shorts.select_segment(records, 15, 59, lines='9-10')


def test_karaoke_keeps_every_character_and_follows_recognized_words():
    face = ImageFont.truetype(FONT, 64)
    text = 'パスキーは、秘密鍵で本人を証明します。100万円のVOICEVOXも安全なのだ。'
    row = rec(1, 'ずんだもん', text, 0.0, 6.0)
    # Speech actually starts after a 2 s pause.
    words = [{'text': text[:len(text) // 2], 'start': 2.0, 'end': 4.0},
             {'text': text[len(text) // 2:], 'start': 4.0, 'end': 5.5}]
    pages, basis = shorts.karaoke_pages(row, words, face, 300, 3)
    assert basis == 'recognized_word_timestamps'
    flat = [(p, t) for page in pages for line in page for p, t in line]
    assert ''.join(p for p, _ in flat) == text
    assert all(len(page) <= 3 for page in pages)
    assert flat[0][1] == pytest.approx(0.0) and flat[1][1] >= 2.0
    assert [t for _, t in flat] == sorted(t for _, t in flat)
    assert any(p == '100万円の' or '100万円' in p for p, _ in flat)
    windows = shorts.page_windows(row, pages)
    assert windows[0][0] == 0.0 and windows[-1][1] == 6.0
    _, fallback = shorts.karaoke_pages(row, [], face, 300, 3)
    assert fallback == 'character_weighted'


def make_episode(tmp_path):
    shell = tmp_path / 'shell'
    for character in ('Zundamon', 'Metan'):
        fixed, mouths, expressions = rs.character_layers(character)
        for n, name in enumerate(sorted(set(fixed + mouths + sum(expressions.values(), [])))):
            path = shell / character / name
            path.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new('RGBA', (200, 400), (0, 0, 0, 0))
            image.paste((40 + n * 3 % 200, 120, 90, 255), (60, 40 + n, 140, 360))
            image.save(path)
    episode = tmp_path / 'episode'
    episode.mkdir()
    rate, seconds = 48000, 3.0
    with wave.open(str(episode / 'narration.wav'), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            t = i / rate
            amp = 0.0 if (t % 1.0) < 0.3 else 0.3 * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * t))
            frames += struct.pack('<h', int(amp * 32767 * math.sin(2 * math.pi * 220 * t)))
        w.writeframes(bytes(frames))
    Image.new('RGB', (800, 450), (30, 80, 160)).save(episode / 'visual.png')
    records = [rec(1, 'ずんだもん', 'ショートの切り出しを試すのだ。', 0.0, 1.5),
               rec(2, '四国めたん', '一語ずつ色が変わる字幕ですわ。', 1.5, 3.0)]
    (episode / 'timing.json').write_text(json.dumps({'records': records}, ensure_ascii=False))
    (episode / 'presentation.json').write_text(json.dumps({
        'title': 'ずんだもんの縦型ショート試験', 'voice_credit': 'VOICEVOX:ずんだもん・四国めたん',
        'visuals': [{'id': 'v1', 'file': 'visual.png', 'source_credit': '利用者提供素材',
                     'source_url': 'https://example.com/a', 'translation': ''}]}, ensure_ascii=False))
    (episode / 'transcription.json').write_text(json.dumps({'words': [
        {'text': 'ショートの', 'start': 0.3, 'end': 0.8}, {'text': '切り出しを試すのだ', 'start': 0.8, 'end': 1.4}]},
        ensure_ascii=False))
    (episode / 'description.txt').write_text('本編\n\nVOICEVOX:ずんだもん\nVOICEVOX:四国めたん\n利用者提供素材 https://example.com/a\n')
    (episode / 'state.json').write_text(json.dumps({'status': 'COMPLETE', 'video_sha256': 'x'}))
    return episode, shell


def test_end_to_end_vertical_short(tmp_path):
    episode, shell = make_episode(tmp_path)
    out = tmp_path / 'short'
    parser = __import__('argparse').ArgumentParser()
    shorts.add_arguments(parser)
    args = parser.parse_args([str(episode), '--out', str(out), '--lines', '1-2', '--shell', str(shell),
                              '--font', FONT, '--cache-root', str(tmp_path / 'cache')])
    report = shorts.make_short(args)
    assert report['segment']['line_ids'] == [1, 2]
    assert report['engines_started_this_run'] == 0 and report['gpu_used'] is False
    assert report['karaoke']['basis'] == ['character_weighted', 'recognized_word_timestamps']
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                                '-of', 'json', str(out / 'short.mp4')]))
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height']) == (1080, 1920)
    assert audio['sample_rate'] == '48000'
    assert abs(float(probe['format']['duration']) - 3.0) < 0.15
    ass = pysubs2.load(str(out / 'short.karaoke.ass'))
    assert all('\\kf' in event.text for event in ass)
    srt = pysubs2.load(str(out / 'short.srt'))
    assert ''.join(e.plaintext.replace('\n', '') for e in srt) == 'ショートの切り出しを試すのだ。一語ずつ色が変わる字幕ですわ。'
    assert '#Shorts' in (out / 'description.txt').read_text()
    assert not list(out.glob('silent-*')) and not list(out.glob('narration-*'))
    with pytest.raises(FileExistsError):
        shorts.make_short(args)


def test_caption_upload_is_dry_run_by_default(tmp_path, capsys):
    from tools import youtube_captions
    srt = tmp_path / 'a.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nテスト\n', encoding='utf-8')
    assert youtube_captions.main(['dQw4w9WgXcQ', str(srt)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['status'] == 'DRY_RUN' and printed['quota_units_if_sent'] == 400
    _, body, content_type = youtube_captions.build_request('dQw4w9WgXcQ', srt)
    assert content_type.startswith('multipart/related; boundary=') and 'テスト'.encode() in body
    with pytest.raises(ValueError):
        youtube_captions.build_request('bad', srt)
