"""工程の原子的な記録、計時、必須QA結果の検証。外部サービスは呼ばない。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import tempfile
import time
import uuid
import wave

RULE_IDS = (
    "R40_shu_at_visual_change", "R41_translation_for_overseas",
    "R42_marker_for_important", "R43_quality_1280", "R44_chapter_switch",
    "R45_peek_on_switch", "R46_tight_crop", "R47_agenda_style",
    "R53_translation_length", "R54_image_not_too_tall",
    "R55_table_se_not_in_card", "R56_marker_limit",
    "R60_no_narration_copy", "R61_summary_limits",
)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def update_json(path, change):
    """置換されるJSONとは別の固定lockを、読取りから保存まで保持する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(value, dict):
            raise ValueError("状態JSONはオブジェクトが必要")
        change(value)
        atomic_json(path, value)
    return value


def update_stage(path, key, value):
    return update_json(path, lambda d: d.__setitem__(key, value))


@contextmanager
def stage(path, key, **details):
    """同一工程の重複起動を拒否し、例外・中断でも失敗と経過秒を保存する。"""
    started = time.monotonic()
    attempt = {**details, "state": "RUNNING", "start": utcnow(), "attempt_id": uuid.uuid4().hex}

    def begin(d):
        previous = d.get(key)
        if isinstance(previous, dict):
            if previous.get("state") == "RUNNING":
                raise RuntimeError(key + ": 既に実行中。中断記録を確認してから再開する")
            d.setdefault("attempt_history", {}).setdefault(key, []).append(previous)
        d[key] = dict(attempt)
    update_json(path, begin)
    result = {"state": "OK"}
    try:
        yield result
    except BaseException as e:
        result.update(state="FAIL", error=type(e).__name__ + ": " + str(e))
        raise
    finally:
        finished = {**attempt, **result, "end": utcnow(),
                    "duration_sec": round(time.monotonic() - started, 6)}

        def finish(d):
            if d.get(key, {}).get("attempt_id") != attempt["attempt_id"]:
                raise RuntimeError(key + ": 実行IDが変わったため上書きを拒否")
            d[key] = finished
        update_json(path, finish)


def wav_info(path, fps=30):
    with wave.open(str(path), "rb") as w:
        samples, rate = w.getnframes(), w.getframerate()
        if samples <= 0 or rate <= 0:
            raise ValueError("空または不正な音声")
        return {"samples": samples, "sample_rate": rate, "channels": w.getnchannels(),
                "sample_width": w.getsampwidth(), "duration": samples / rate,
                "expected_frames": (samples * fps + rate - 1) // rate}


def assess_rules(path, returncode):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("規則結果はオブジェクトが必要")
        missing = [key for key in RULE_IDS if key not in value]
        if missing:
            raise ValueError("必須結果がない: " + ", ".join(missing))
        allowed = {"PASS", "FAIL", "NOT_CHECKED", "NOT_IMPLEMENTED"}
        if any(not isinstance(v, dict) or v.get("status") not in allowed for v in value.values()):
            raise ValueError("規則結果の状態または形式が不正")
        failures = [key for key, v in value.items() if v["status"] == "FAIL"]
        pending = [key for key, v in value.items() if v["status"] in {"NOT_CHECKED", "NOT_IMPLEMENTED"}]
        return {"state": "FAIL" if returncode or failures else ("WAIT" if pending else "OK"),
                "exit_code": returncode, "failures": failures, "pending": pending,
                "rules_sha256": sha256(path)}
    except (OSError, ValueError, TypeError) as e:
        return {"state": "FAIL", "exit_code": returncode, "error": str(e)}


def assess_version(path, returncode, audio):
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("版QA結果はオブジェクトが必要")
        expected = wav_info(audio)["expected_frames"]
        if returncode or type(report.get("frames")) is not int or report["frames"] != expected:
            raise ValueError("版QAの異常終了またはフレーム数不一致")
        if report.get("machine_status") != "PASS":
            raise ValueError("版QAの合格記録がない")
        return {"state": "OK", "report_sha256": sha256(path), "frames": expected}
    except (OSError, ValueError, TypeError, wave.Error) as e:
        return {"state": "FAIL", "error": str(e), "exit_code": returncode}


def task_input_snapshot(task):
    """承認・音声を複製せず、台本と受領画像の版への結び付きを保存する。"""
    task=Path(task).resolve()
    receipt=task/'authoring-receipt.json'
    names=set(json.loads(receipt.read_text())['files']) if receipt.exists() else {'work/content.py','work/script.txt','material-sheet-v1.json'}
    if receipt.exists():names.add('authoring-receipt.json')
    if (task/'capture/materials-receipt.json').is_file():
        from material_prepare import resolved_materials
        names.add('capture/materials-receipt.json')
        resolved=resolved_materials(task)
        names.update(x['file'] for x in resolved)
        names.update(x['capture_manifest'] for x in resolved if x.get('capture_manifest'))
        names.update(str(p.relative_to(task)) for p in (task/'sources').glob('*') if p.is_file())
    else:
        names.update(x['file'] for x in json.loads((task/'material-sheet-v1.json').read_text())['items'])
    result={}
    for name in sorted(names):
        path=(task/name).resolve()
        if not path.is_relative_to(task):raise ValueError('task入力のパスが範囲外')
        result[name]=sha256(path)
    return result


def verify_task_inputs(source,task=None):
    """新しい版のtask入力スナップショットを検査し、G1の依存へ返す。"""
    if 'task_inputs' not in source and 'task_root' not in source:return []
    if not isinstance(source.get('task_root'),str) or not source['task_root']:raise ValueError('版のtask入力元が不正')
    root=Path(source['task_root']).resolve();hashes=source['task_inputs']
    if task is not None and root!=Path(task).resolve():raise ValueError('版のtaskが異なる')
    if not isinstance(hashes,dict) or not hashes:raise ValueError('版のtask入力記録が空または不正')
    paths=[]
    for name,digest in hashes.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or sha256(path)!=digest:raise ValueError('版作成後にtask入力が変化: '+name)
        paths.append(path)
    return paths


def verify_version_task(version,task,audio,timeline):
    """描画前後で共通の、音声・台本・図解計画の再開元照合。"""
    version,task,audio,timeline=map(lambda p:Path(p).resolve(),(version,task,audio,timeline))
    source=json.loads((version/'manifest.json').read_text())['source']
    if (source.get('audio')!=str(audio) or source.get('audio_sha256')!=sha256(audio)
            or source.get('timing_sha256')!=sha256(timeline)
            or source.get('content_sha256')!=sha256(task/'work/content.py')):
        raise ValueError('再開する版と指定音声・timeline・台本が異なる')
    verify_task_inputs(source,task)
    pack=json.loads((task/'story-pack.json').read_text())
    saved=version/'inputs/motion-source.json'
    if saved.exists():
        if json.loads(saved.read_text())!=pack:raise ValueError('既存版とtaskの台本・図解計画が異なる')
    elif 'motion_plan' in pack:raise ValueError('既存版に指定taskの図解計画がない')
    return source


def verify_render_receipt(version, expected_shell=None):
    version = Path(version)
    receipt = json.loads((version / "render/render_receipt.json").read_text(encoding="utf-8"))
    if not isinstance(receipt,dict) or not isinstance(receipt.get("input_hashes"), dict) or not receipt["input_hashes"]:
        raise ValueError("再開に必要な入力ハッシュがない")
    if 'source' in receipt and receipt['source']!=json.loads((version/'manifest.json').read_text()).get('source',{}):
        raise ValueError('再開時に版の受領元が変化')
    requested_shell=expected_shell or os.environ.get('VIDEO_SHELL_DIR')
    if requested_shell and (not receipt.get('shell') or Path(requested_shell).resolve()!=Path(receipt['shell']).resolve()):
        raise ValueError('再開時に指定人物shellが描画受領と異なる。新しい版を作成する')
    if receipt.get('shell'):
        files=sorted(str(p.resolve()) for p in Path(receipt['shell']).rglob('*') if p.is_file())
        if files!=receipt.get('shell_files'):raise ValueError('再開時に人物shellのファイル一覧が変化')
    for path, digest in receipt["input_hashes"].items():
        if sha256(path) != digest:
            raise ValueError("再開時に入力・設定・コードが変化: " + path)
    if sha256(version / "render/video.mp4") != receipt.get("video_sha256"):
        raise ValueError("再開時に最終動画のSHAが変化")
    return receipt


def record_render_receipt(version):
    version = Path(version)
    preflight = json.loads((version / "render/preflight.json").read_text(encoding="utf-8"))
    if preflight.get("state") != "OK" or not preflight.get("hashes"):
        raise ValueError("描画前検査の合格記録がない")
    for path, digest in preflight["hashes"].items():
        if sha256(path) != digest:
            raise ValueError("描画中に入力・設定・コードが変化: " + path)
    shell=os.environ.get('VIDEO_SHELL_DIR')
    receipt = {"source":json.loads((version/'manifest.json').read_text()).get('source',{}),
               'shell':shell,'shell_files':sorted(str(p.resolve()) for p in Path(shell).rglob('*') if p.is_file()) if shell else [],
               "input_hashes": preflight["hashes"], "video_sha256": sha256(version / "render/video.mp4"),
               "audio": wav_info(version / "inputs/audio.wav"), "recorded_at": utcnow()}
    atomic_json(version / "render/render_receipt.json", receipt)
    update_json(version / "manifest.json", lambda d: d.update(render=receipt))
    return receipt


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("record-render", "verify-render"))
    ap.add_argument("version", type=Path)
    a = ap.parse_args()
    result = record_render_receipt(a.version) if a.action == "record-render" else verify_render_receipt(a.version)
    print(json.dumps(result, ensure_ascii=False))
