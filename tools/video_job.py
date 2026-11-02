"""承認済みの動画テーマ（content-engine が作る job JSON）から、制作の入口を作る。
  python tools/video_job.py list --jobs 承認フォルダ
      承認済みのテーマを一覧する（何も作らない・費用なし）
  python tools/video_job.py plan --job 承認フォルダ\\<id>.json --out 新しい出力先
      調査の依頼JSONを作る（費用なし。Geminiは呼ばない）
  python tools/video_job.py prepare --job 承認フォルダ\\<id>.json --out 新しい出力先 --run-paid
      Geminiで調査・撮影・台本を作る。費用が出るため --run-paid を付けた時だけ動く

その後の音声合成・描画（Colab GPU）と YouTube への非公開アップロードは、
それぞれ別の手順で行う。このファイルからは実行しない。"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = re.compile(r"^[0-9a-f]{12}$")


def load_job(path):
    job = json.loads(Path(path).read_text(encoding="utf-8-sig"))  # BOM付きでも読めるように
    if not JOB_ID.match(str(job.get("id", ""))):
        raise ValueError(f"ジョブIDが不正です: {path}")
    if job.get("status") != "approved":
        raise ValueError(f"承認されていないジョブです: {job.get('id')}")
    if not str(job.get("topic", "")).strip():
        raise ValueError(f"テーマが空です: {job.get('id')}")
    if any(not str(url).startswith("https://") for url in job.get("urls", [])):
        raise ValueError(f"参考URLは https:// のみ使えます: {job.get('id')}")
    return job


def list_jobs(directory):
    found = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            job = load_job(path)
        except ValueError:
            continue
        found.append(job)
    return found


def request_from_job(job):
    """prepare_project.py が読む依頼の形（Gemini で調査・台本を作る）。"""
    return {"topic": job["topic"].strip(), "urls": list(job.get("urls", [])), "provider": "gemini",
            "minutes": 10, "max_sources": 8, "job_id": job["id"]}


def write_plan(job, out):
    out = Path(out)
    if out.exists():
        raise FileExistsError("新しい出力先を指定してください（既存のフォルダは使わない）")
    out.mkdir(parents=True)
    request = request_from_job(job)
    path = out / "request.json"
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_prepare(job, out, run_paid):
    if not run_paid:
        raise SystemExit("調査と台本はGeminiの費用が出ます。実行するなら --run-paid を付けてください")
    request_path = write_plan(job, out)
    env = dict(os.environ, PYTHONUTF8="1")
    subprocess.run([sys.executable, str(ROOT / "tools" / "prepare_project.py"),
                    "--request", str(request_path), "--output", str(Path(out) / "prepared")], env=env, check=True)
    return Path(out) / "prepared"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="cmd", required=True)
    lst = commands.add_parser("list")
    lst.add_argument("--jobs", required=True, type=Path)
    plan = commands.add_parser("plan")
    plan.add_argument("--job", required=True, type=Path)
    plan.add_argument("--out", required=True, type=Path)
    prep = commands.add_parser("prepare")
    prep.add_argument("--job", required=True, type=Path)
    prep.add_argument("--out", required=True, type=Path)
    prep.add_argument("--run-paid", action="store_true", help="Geminiの費用が出る調査・台本を実行する")
    args = parser.parse_args()
    if args.cmd == "list":
        for job in list_jobs(args.jobs):
            print(f"{job['id']}  {job['topic']}")
        return 0
    job = load_job(args.job)
    if args.cmd == "plan":
        print(f"依頼JSONを作りました: {write_plan(job, args.out)}")
        return 0
    result = run_prepare(job, args.out, args.run_paid)
    print(f"調査・台本の出力: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
