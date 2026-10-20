"""Install the official, pinned Linux CPU release; models stay out of Git."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request

VERSION = "0.25.2"
ARCHIVE = f"voicevox_engine-linux-cpu-x64-{VERSION}.7z.001"
URL = f"https://github.com/VOICEVOX/voicevox_engine/releases/download/{VERSION}/{ARCHIVE}"
SHA256 = "bab016a966131b89bad398e7b898f8c742617dc80a7da49bf494cd07133dbc7a"


def install(destination):
    destination = Path(destination).expanduser().resolve()
    receipt = destination / "install-receipt.json"
    if receipt.is_file() and (destination / "run").is_file():
        previous = json.loads(receipt.read_text())
        if previous.get("archive_sha256") != SHA256:
            raise ValueError("Engine cache belongs to another release")
        print(f"VOICEVOX CPU {VERSION}: cached", flush=True)
        return destination
    if destination.exists():
        raise FileExistsError(f"Incomplete engine directory: {destination}")
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise FileNotFoundError("Install p7zip-full or 7zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="voicevox-install-", dir=destination.parent) as temporary:
        folder = Path(temporary)
        archive = folder / ARCHIVE
        digest, count, reported = hashlib.sha256(), 0, 0
        with urllib.request.urlopen(URL, timeout=180) as response, archive.open("wb") as output:
            while block := response.read(8 * 1024 * 1024):
                output.write(block)
                digest.update(block)
                count += len(block)
                if count - reported >= 256 * 1024 * 1024:
                    print(f"VOICEVOX CPU download: {count // (1024*1024)} MiB", flush=True)
                    reported = count
        if digest.hexdigest() != SHA256:
            raise ValueError("Official release archive SHA-256 mismatch")
        extracted = folder / "extracted"
        subprocess.run([sevenzip, "x", str(archive), f"-o{extracted}", "-y", "-bsp0", "-bso0"], check=True)
        candidates = list(extracted.rglob("run"))
        if len(candidates) != 1:
            raise ValueError("Official archive must contain exactly one engine executable")
        engine = candidates[0].parent
        candidates[0].chmod(candidates[0].stat().st_mode | 0o111)
        (engine / "install-receipt.json").write_text(json.dumps({
            "version": VERSION, "device": "cpu", "source": URL,
            "archive_sha256": SHA256, "download_bytes": count,
            "install_seconds": round(time.monotonic() - started, 3)}, indent=2) + "\n")
        engine.rename(destination)
    print(f"VOICEVOX CPU {VERSION}: installed", flush=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    install(args.directory)
