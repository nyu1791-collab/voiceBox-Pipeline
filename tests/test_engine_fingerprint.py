from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine_pool
from engine_pool import engine_fingerprint, engine_fingerprint_cache


@pytest.fixture
def engine(tmp_path):
    root = tmp_path / 'engine'
    (root / 'model').mkdir(parents=True)
    (root / 'run').write_bytes(b'official-engine-fixture')
    (root / 'model/voice.bin').write_bytes(b'voice-model-fixture')
    return root


def test_cache_hit_does_not_reread_large_engine_files(engine, tmp_path, monkeypatch):
    cache = tmp_path / 'cache/fingerprint.json'
    expected = engine_fingerprint(engine, cache)
    original = Path.open
    def checked_open(path, mode='r', *args, **kwargs):
        if 'b' in mode and path.is_relative_to(engine):
            pytest.fail('Unchanged engine files were rehashed')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', checked_open)
    assert engine_fingerprint(engine, cache) == expected


def test_model_changes_invalidate_the_cache(engine, tmp_path):
    cache = tmp_path / 'fingerprint.json'
    first = engine_fingerprint(engine, cache)
    (engine / 'model/voice.bin').write_bytes(b'updated-model-with-different-size')
    second = engine_fingerprint(engine, cache)
    assert second != first
    assert second == engine_fingerprint(engine)


@pytest.mark.parametrize('bad', ['{invalid', '[]', '{"sha256":null}'])
def test_broken_metadata_is_rebuilt(engine, tmp_path, bad):
    cache = tmp_path / 'fingerprint.json'
    expected = engine_fingerprint(engine)
    cache.write_text(bad)
    assert engine_fingerprint(engine, cache) == expected
    assert json.loads(cache.read_text())['sha256'] == expected


def test_invalid_cached_digest_is_not_accepted(engine, tmp_path):
    cache = tmp_path / 'fingerprint.json'
    expected = engine_fingerprint(engine, cache)
    metadata = json.loads(cache.read_text())
    metadata['sha256'] = 'invalid-digest'
    cache.write_text(json.dumps(metadata))
    assert engine_fingerprint(engine, cache) == expected


def test_parallel_writers_use_separate_temporary_files(engine, tmp_path, monkeypatch):
    cache = tmp_path / 'cache/fingerprint.json'
    expected = engine_fingerprint(engine)
    original = engine_pool.NamedTemporaryFile
    barrier = threading.Barrier(2)
    def concurrent_temp(*args, **kwargs):
        handle = original(*args, **kwargs)
        barrier.wait(timeout=5)
        return handle
    monkeypatch.setattr(engine_pool, 'NamedTemporaryFile', concurrent_temp)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: engine_fingerprint(engine, cache), range(2)))
    assert results == [expected, expected]
    assert json.loads(cache.read_text())['sha256'] == expected
    assert not list(cache.parent.glob('*.tmp'))


def test_unwritable_cache_does_not_block_production(engine, tmp_path, monkeypatch):
    cache = tmp_path / 'cache/fingerprint.json'
    expected = engine_fingerprint(engine)
    original = Path.replace
    def denied(path, target):
        if target == cache:
            raise PermissionError('fixture cache is read-only')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', denied)
    assert engine_fingerprint(engine, cache) == expected
    assert not list(cache.parent.glob('*.tmp'))


def test_different_checkouts_share_only_engine_metadata(engine, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'user-cache'))
    first, second = tmp_path / 'checkout-a', tmp_path / 'checkout-b'
    first.mkdir(); second.mkdir()
    monkeypatch.chdir(first)
    a = engine_fingerprint_cache(engine)
    monkeypatch.chdir(second)
    b = engine_fingerprint_cache(engine)
    assert a == b and a.parent == tmp_path / 'user-cache/voicebox-tools/engine-fingerprints'
