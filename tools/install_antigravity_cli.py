"""Install the official Linux x64 CLI pinned to a verified public release."""
import argparse
import hashlib
from pathlib import Path
import tarfile
import tempfile
import urllib.request

VERSION = "1.4.3"
URL = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.4.3-4577820882632704/linux-x64/cli_linux_x64.tar.gz"
SHA512 = "9b726b86280a56e8ffa84b3b3a1c00d19972833a1f6c0b21d85c5fc6e408af6492e4c61353cad9b500bee599af264c92cfc2ead1f5048ab9525328e1e4199c40"


def install(directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "agy"
    if target.exists():
        raise FileExistsError("Refusing to overwrite an existing CLI")
    with tempfile.TemporaryDirectory(prefix="antigravity-install-") as temporary:
        archive = Path(temporary) / "cli.tar.gz"
        digest = hashlib.sha512()
        with urllib.request.urlopen(URL, timeout=180) as source, archive.open("wb") as out:
            while block := source.read(1024 * 1024):
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != SHA512:
            raise ValueError("Official CLI archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as package:
            members = [member for member in package.getmembers()
                       if member.name == "antigravity" and member.isfile()]
            if len(members) != 1:
                raise ValueError("Expected exactly one CLI binary")
            source = package.extractfile(members[0])
            with source, target.open("xb") as out:
                while block := source.read(1024 * 1024):
                    out.write(block)
        target.chmod(0o700)
    print("Official Antigravity CLI " + VERSION + ": installed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    install(parser.parse_args().directory)
