import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tools import install_voicevox_gpu as gpu


def test_download_rejects_bad_release_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'bad'))
    with pytest.raises(ValueError, match='checksum mismatch'):
        gpu.download('https://example.invalid/part', tmp_path / 'part', hashlib.sha256(b'good').hexdigest())


def test_cuda_has_separate_cache_key():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, VOICEBOX_DEVICE='cuda', PYTHONUTF8='1')
    code = "import pipeline; print(pipeline.WORKERS, pipeline.cache_entry({'speaker':'ずんだもん','text':'確認'}, 'abc')[1]['device'])"
    result = subprocess.check_output([sys.executable, '-c', code], cwd=root, env=env, text=True)
    assert result.strip() == '1 cuda'
