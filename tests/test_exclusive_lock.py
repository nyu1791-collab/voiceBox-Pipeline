from pathlib import Path
from types import SimpleNamespace
import errno
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'renderer')]
import material_prepare
import motion_assets
import pipeline
import pipeline_common
from pipeline_common import exclusive_lock, update_json

HOLD = """
import sys
sys.path.insert(0, sys.argv[1])
from pipeline_common import exclusive_lock
print('STARTED', flush=True)
with exclusive_lock(sys.argv[2]):
    print('HELD', flush=True)
    sys.stdin.readline()
"""


def spawn(path):
    return subprocess.Popen([sys.executable, '-c', HOLD, str(ROOT / 'renderer'), str(path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


@pytest.fixture
def holder(tmp_path):
    process = spawn(tmp_path / 'x.lock')
    assert process.stdout.readline().strip() == 'STARTED'
    assert process.stdout.readline().strip() == 'HELD'
    yield process
    process.kill()
    process.communicate()


def assert_busy(path):
    with pytest.raises(RuntimeError, match='BUSY'):
        with exclusive_lock(path, blocking=False):
            pass


@pytest.mark.parametrize('try_lock', [lambda p: exclusive_lock(p, blocking=False), pipeline.lock],
                         ids=['exclusive_lock', 'pipeline.lock'])
def test_non_blocking_is_busy_across_processes_and_free_after_exit(tmp_path, holder, try_lock):
    path = tmp_path / 'x.lock'
    with pytest.raises(RuntimeError, match='BUSY'):
        with try_lock(path):
            pass
    holder.stdin.write('\n')
    holder.stdin.flush()
    assert holder.wait(timeout=10) == 0
    with try_lock(path):
        pass


def test_blocking_waits_for_the_other_process(tmp_path, holder):
    waiter = spawn(tmp_path / 'x.lock')
    try:
        assert waiter.stdout.readline().strip() == 'STARTED'
        time.sleep(0.5)
        waiter.stdin.write('\n')
        waiter.stdin.flush()
        with pytest.raises(subprocess.TimeoutExpired):
            waiter.wait(timeout=1)
        holder.stdin.write('\n')
        holder.stdin.flush()
        assert waiter.stdout.readline().strip() == 'HELD'
        assert waiter.wait(timeout=10) == 0
    finally:
        waiter.kill()
        waiter.communicate()


def test_killed_holder_releases_the_lock(tmp_path, holder):
    path = tmp_path / 'x.lock'
    assert_busy(path)
    holder.kill()
    holder.wait(timeout=10)
    deadline = time.monotonic() + 5
    while True:
        try:
            with exclusive_lock(path, blocking=False):
                return
        except RuntimeError:
            assert time.monotonic() < deadline
            time.sleep(0.05)


@pytest.mark.parametrize('first, second', [(pipeline.lock, exclusive_lock), (exclusive_lock, pipeline.lock)])
def test_both_copies_share_one_lock_within_a_process(tmp_path, first, second):
    path = tmp_path / 'deep/x.lock'
    with first(path):
        with pytest.raises(RuntimeError, match='BUSY'):
            with second(path, **({'blocking': False} if second is exclusive_lock else {})):
                pass
    with second(path):
        pass


@pytest.mark.parametrize('make', [exclusive_lock, pipeline.lock])
def test_body_errors_pass_through_and_release(tmp_path, make):
    path = tmp_path / 'x.lock'
    with pytest.raises(PermissionError):
        with make(path):
            raise PermissionError(errno.EACCES, 'from the body, not a busy lock')
    with make(path):
        pass


@pytest.mark.skipif(os.name != 'nt', reason='msvcrt only')
@pytest.mark.parametrize('make', [lambda p: exclusive_lock(p, blocking=False), pipeline.lock])
def test_windows_real_os_error_is_not_reported_as_busy(tmp_path, monkeypatch, make):
    import msvcrt

    def fail(*_):
        raise OSError(errno.EBADF, 'bad descriptor')
    monkeypatch.setattr(msvcrt, 'locking', fail)
    with pytest.raises(OSError) as caught:
        with make(tmp_path / 'x.lock'):
            pass
    assert caught.value.errno == errno.EBADF


def test_lock_file_stays_small_and_data_file_stays_readable(tmp_path):
    data, path = tmp_path / 'state.json', tmp_path / 'state.json.lock'
    data.write_text('{}')
    for _ in range(2):
        with exclusive_lock(path):
            assert data.read_text() == '{}'
    assert path.stat().st_size == (1 if os.name == 'nt' else 0)


@pytest.mark.parametrize('blocking, flags', [(True, 2), (False, 6)])
def test_posix_branch_uses_flock(tmp_path, monkeypatch, blocking, flags):
    calls = []
    fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda handle, operation: calls.append(operation))
    monkeypatch.setitem(sys.modules, 'fcntl', fake)
    monkeypatch.setattr(pipeline_common, 'os', SimpleNamespace(name='posix'))
    with exclusive_lock(tmp_path / 'x.lock', blocking=blocking):
        assert calls == [flags]
    assert (tmp_path / 'x.lock').stat().st_size == 0


def test_posix_branch_maps_only_would_block_to_busy(tmp_path, monkeypatch):
    def would_block(handle, operation):
        raise BlockingIOError(errno.EWOULDBLOCK, 'held')
    fake = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=would_block)
    monkeypatch.setitem(sys.modules, 'fcntl', fake)
    monkeypatch.setattr(pipeline_common, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(pipeline, 'os', SimpleNamespace(name='posix'))
    with pytest.raises(RuntimeError, match='BUSY'):
        with exclusive_lock(tmp_path / 'x.lock', blocking=False):
            pass
    with pytest.raises(RuntimeError, match='BUSY'):
        with pipeline.lock(tmp_path / 'y.lock'):
            pass


def test_update_json_holds_its_dedicated_lock(tmp_path, monkeypatch):
    # atomic_json's directory fsync uses os.O_DIRECTORY, which Windows lacks: a separate gap.
    monkeypatch.setattr(pipeline_common, 'atomic_json',
                        lambda path, value: Path(path).write_text(json.dumps(value)))
    path = tmp_path / 'state.json'
    update_json(path, lambda d: (assert_busy(tmp_path / 'state.json.lock'), d.update(a=1)))
    assert json.loads(path.read_text()) == {'a': 1}


def test_import_clip_holds_its_dedicated_lock(tmp_path, monkeypatch):
    path = tmp_path / 'inputs/.motion-assets.lock'
    monkeypatch.setattr(motion_assets, '_import_clip', lambda *_: assert_busy(path) or 'done')
    assert motion_assets.import_clip(tmp_path, {}, None, None, None) == 'done'


def test_prepare_holds_its_dedicated_lock(tmp_path, monkeypatch):
    path = tmp_path / 'capture/.prepare-materials.lock'
    monkeypatch.setattr(material_prepare, 'current_receipt', lambda task: {})
    monkeypatch.setattr(material_prepare, 'verify', lambda task: assert_busy(path) or {'state': 'OK'})
    assert material_prepare.prepare(tmp_path)['reused'] is True
