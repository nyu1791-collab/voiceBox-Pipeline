"""Install the official split Linux NVIDIA engine, verifying both release parts."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request

VERSION = '0.25.2'
BASE = f'https://github.com/VOICEVOX/voicevox_engine/releases/download/{VERSION}/'
PARTS = {
    f'voicevox_engine-linux-nvidia-{VERSION}.7z.001': 'b7e2fb54ed6abca3f4003982e97e8496a254a8d0b93ade4a9dd6a3fcca946479',
    f'voicevox_engine-linux-nvidia-{VERSION}.7z.002': '775d0c495535d44cab77ec219b80e2495a7e151f61ffda70489c4d0d96d52d04',
}


def download(url, path, expected):
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=180) as source, path.open('xb') as target:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
            target.write(block)
    if digest.hexdigest() != expected:
        raise ValueError(f'Official archive checksum mismatch: {path.name}')


def install(destination):
    destination = Path(destination).expanduser().resolve()
    receipt = destination / 'install-receipt.json'
    if receipt.is_file() and (destination / 'run').is_file():
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if saved.get('parts') != PARTS or saved.get('device') != 'nvidia':
            raise ValueError('Engine cache does not match this NVIDIA release')
        return destination
    if destination.exists():
        raise FileExistsError(f'Incomplete engine directory: {destination}')
    sevenzip = shutil.which('7z') or shutil.which('7zz')
    if not sevenzip:
        raise FileNotFoundError('7z is required')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voicevox-gpu-', dir=destination.parent) as temporary:
        folder = Path(temporary)
        for name, digest in PARTS.items():
            print(f'Downloading {name}', flush=True)
            download(BASE + name, folder / name, digest)
        extracted = folder / 'extracted'
        subprocess.run([sevenzip, 'x', str(folder / next(iter(PARTS))), f'-o{extracted}', '-y', '-bsp0', '-bso0'], check=True)
        candidates = list(extracted.rglob('run'))
        if len(candidates) != 1 or not candidates[0].is_file():
            raise ValueError('Official archive must contain one engine executable')
        engine = candidates[0].parent
        candidates[0].chmod(candidates[0].stat().st_mode | 0o111)
        (engine / 'install-receipt.json').write_text(json.dumps({'version': VERSION, 'device': 'nvidia', 'parts': PARTS, 'source': BASE}, indent=2) + '\n', encoding='utf-8')
        engine.rename(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    print(install(parser.parse_args().directory))
