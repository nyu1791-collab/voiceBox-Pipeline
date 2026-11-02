from pathlib import Path
import json
import sys
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline

ROW = {'id': 1, 'speaker': 'ずんだもん', 'text': '字幕', 'speech_text': 'じまく', 'emotion': 'NORMAL', 'visual': 'v'}


def key(row=ROW, engine='e' * 64):
    return pipeline.cache_entry(row, engine)[0].name


def write_wav(path):
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b'\0\0' * 480)


def seed_cache(directory, specification):
    directory.mkdir(parents=True)
    write_wav(directory / 'raw.wav')
    write_wav(directory / 'voice.wav')
    (directory / 'query.json').write_text('{}')
    files = {name: pipeline.sha256(directory / name) for name in ('raw.wav', 'voice.wav', 'query.json')}
    (directory / 'receipt.json').write_text(json.dumps({'specification': specification, 'files': files}))


def test_key_does_not_depend_on_source_files(tmp_path, monkeypatch):
    before = key()
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    monkeypatch.setattr(pipeline, 'sha256', lambda path: pytest.fail('hashed ' + str(path)))
    assert key() == before
    assert pipeline.cache_entry(ROW, 'e' * 64)[1]['synth_revision'] == pipeline.SYNTH_REVISION


@pytest.mark.parametrize('name, value', [('SYNTH_REVISION', 'other'), ('SPEED', 1.0), ('ATEMPO', 1.05)])
def test_synthesis_settings_change_the_key(monkeypatch, name, value):
    before = key()
    monkeypatch.setattr(pipeline, name, value)
    assert key() != before


def test_speech_inputs_change_the_key():
    assert key(dict(ROW, speech_text='べつ')) != key()
    assert key(dict(ROW, speaker='四国めたん')) != key()
    assert key(engine='f' * 64) != key()
    assert key(dict(ROW, text='字幕だけ変更', emotion='HAPPY', visual='w')) == key()


def test_old_code_hash_receipt_is_not_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    directory, specification, _ = pipeline.cache_entry(ROW, 'e' * 64)
    old = {k: v for k, v in specification.items() if k != 'synth_revision'}
    seed_cache(directory, {**old, 'synth_code_sha256': 'c' * 64})
    assert pipeline.cache_entry(ROW, 'e' * 64)[2] is False


def test_prepare_audio_reuses_cache_without_engines_or_source_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)  # holds no pipeline.py or engine_pool.py
    monkeypatch.setattr(pipeline, 'EnginePool', lambda *_: pytest.fail('engine started'))
    directory, specification, _ = pipeline.cache_entry(ROW, 'e' * 64)
    seed_cache(directory, specification)
    out = tmp_path / 'out'
    out.mkdir()
    records, report = pipeline.prepare_audio({'lines': [ROW]}, {}, out, 'e' * 64)
    assert (report['reused_lines'], report['new_unique_lines']) == (1, 0)
    assert records[0]['cache'] == str(directory)
