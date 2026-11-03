"""動画の材料（project.json と materials）を Colab の GPU で描画し、MP4 を手元に戻す。
使い方:
  python tools/rumor_video/render_project.py --project-dir <材料のフォルダ> --out-dir <結果の保存先> [--attempts 3]
認証: gcloud の ADC（許可したアカウントだけ）。鍵や認証の値は表示しない。
終わったら必ずランタイムを止める。接続が切れた場合は、新しいランタイムで最初からやり直す（最大 --attempts 回）。"""
import argparse
import base64
import io
import json
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path

ASSETS_DEFAULT = Path(r"C:\Users\nyu17\yu-lab\media\input\zundamon-assets")
ALLOWED_ACCOUNTS = {"n.yu17919191@gmail.com", "n.yu1791919191@gmail.com"}
CHUNK = 400_000
REPO = "https://github.com/nyu1791-collab/voiceBox-Pipeline"


def build_package(project_dir, assets, package):
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    used = sorted({v["file"] for v in project["visuals"]})
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(project_dir / "project.json", "project/project.json")
        for rel in used:
            z.write(project_dir / rel, "project/" + rel)
        for path in (assets / "characters").rglob("*"):
            if path.is_file():
                z.write(path, "assets/characters/" + path.relative_to(assets / "characters").as_posix())
        for path in (assets / "fonts").glob("*.ttf"):
            z.write(path, "assets/fonts/" + path.name)
        for path in (assets / "backgrounds").glob("*.jpg"):
            z.write(path, "assets/backgrounds/" + path.name)
    print(f"package: {package.stat().st_size / 1e6:.1f} MB ({len(used)} screenshots)", flush=True)


def connect():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials
    from colab_cli.client import Client, Prod
    info = json.loads((Path(os.environ["APPDATA"]) / "gcloud" / "application_default_credentials.json").read_text(encoding="utf-8"))
    session = AuthorizedSession(Credentials.from_authorized_user_info(info))
    identity = session.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=30).json()
    if identity.get("email", "").lower() not in ALLOWED_ACCOUNTS:
        raise SystemExit("許可していないアカウントです。何も起動していません")
    return Client(Prod(), session)


def one_attempt(package, out_dir, assets):
    from colab_cli.client import Accelerator, Shape, Variant
    from colab_cli.runtime import ColabRuntime
    client = connect()
    before = float(client.get_consumption_user_info().paid_compute_units_balance)
    print(f"balance before: {before:.3f}", flush=True)
    assignment = client.assign(uuid.uuid4(), variant=Variant.GPU, accelerator=Accelerator.L4, shape=Shape.STANDARD)
    print("granted L4", flush=True)
    runtime = ColabRuntime(assignment.runtime_proxy_info.url, assignment.runtime_proxy_info.token)

    def run(code, label, timeout=3600):
        outputs = runtime.execute_code(code, allow_stdin=False, timeout=timeout)
        text = "".join(item.get("text", "") for item in outputs if item.get("output_type") == "stream")
        errors = [item for item in outputs if item.get("output_type") == "error"]
        if errors:
            print(f"[{label}] ERROR {errors[0].get('ename')}: {errors[0].get('evalue')}", flush=True)
            print(text[-2000:], flush=True)
            raise RuntimeError(label)
        print(f"[{label}] ok", flush=True)
        return text

    try:
        run("import os; os.makedirs('/content/work', exist_ok=True); print('ok')", "dirs")
        run(f"import subprocess; r=subprocess.run(['git','clone','--depth','1','{REPO}','/content/voicebox'],capture_output=True,text=True); print(r.returncode)", "clone")
        run("import subprocess; r=subprocess.run(['pip','install','-q','-r','/content/voicebox/requirements.txt'],capture_output=True,text=True); print(r.returncode)", "pip", timeout=3600)
        run("import sys; sys.path.insert(0,'/content/voicebox'); from pathlib import Path; from tools.install_voicevox_gpu import install; print(install(Path('/content/voicevox')))", "engine", timeout=3600)
        encoded = base64.b64encode(package.read_bytes()).decode()
        run("open('/content/pkg.b64','w').close(); print('init')", "upload-init")
        pieces = list(range(0, len(encoded), CHUNK))
        for n, start in enumerate(pieces):
            run(f"open('/content/pkg.b64','a').write({encoded[start:start + CHUNK]!r}); print('{n}')", f"upload {n + 1}/{len(pieces)}")
        run("import base64,zipfile,io; zipfile.ZipFile(io.BytesIO(base64.b64decode(open('/content/pkg.b64').read()))).extractall('/content/work'); print('unzipped')", "unzip")
        render = run("""
import os, subprocess, sys
os.environ['COLAB_GPU'] = '1'
os.environ['PYTHONUTF8'] = '1'
r = subprocess.run([sys.executable, '/content/voicebox/tools/colab_gpu_run.py',
    '--project', '/content/work/project/project.json', '--out', '/content/out',
    '--engine-dir', '/content/voicevox',
    '--shell', '/content/work/assets/characters/Shikokumetan_Zundamon_Shell(20230806)',
    '--font', '/content/work/assets/fonts/ZenMaruGothic-Medium.ttf',
    '--background', '/content/work/assets/backgrounds/blackboard-user.jpg'],
    cwd='/content/voicebox', capture_output=True, text=True)
print('exit', r.returncode)
print(r.stderr[-1500:])
""", "render", timeout=7200)
        if "exit 0" not in render:
            raise RuntimeError("render failed")
        encoded_out = run("import base64; print(base64.b64encode(open('/content/out/video.mp4','rb').read()).decode().__len__())", "size").strip()
        total = int(encoded_out)
        run("import base64; open('/content/out.b64','w').write(base64.b64encode(open('/content/out/video.mp4','rb').read()).decode()); print('ok')", "encode")
        parts = []
        for start in range(0, total, CHUNK):
            parts.append(run(f"print(open('/content/out.b64').read()[{start}:{start + CHUNK}], end='')", f"fetch {start // CHUNK + 1}"))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "video.mp4").write_bytes(base64.b64decode("".join(parts)))
        receipt = run("print(open('/content/out/colab-gpu-receipt.json').read())", "receipt")
        (out_dir / "colab-gpu-receipt.json").write_text(receipt, encoding="utf-8")
        print("saved:", out_dir / "video.mp4", flush=True)
    finally:
        client.unassign(assignment.endpoint)
        after = float(client.get_consumption_user_info().paid_compute_units_balance)
        print(f"released. used {before - after:.3f} units", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--assets", type=Path, default=ASSETS_DEFAULT)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    package = args.out_dir.with_suffix(".package.zip")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    build_package(args.project_dir, args.assets, package)
    for attempt in range(1, args.attempts + 1):
        print(f"attempt {attempt}", flush=True)
        try:
            one_attempt(package, args.out_dir, args.assets)
            return 0
        except RuntimeError as error:
            print(f"attempt {attempt} stopped: {error}", flush=True)
            time.sleep(10)
    return 1


if __name__ == "__main__":
    sys.exit(main())
