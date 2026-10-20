#!/usr/bin/env python3
"""project.json -> ten CPU voices -> one video encode and audio mux."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import argparse
import fcntl
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave

from engine_pool import EnginePool, WORKERS, engine_fingerprint, engine_fingerprint_cache
from media_editing import prepare_visual, mux_audio, sound_effects, make_thumbnail
from subtitle_tools import write_subtitles

ROOT = Path(__file__).resolve().parent
SPEAKERS = {"ずんだもん": 3, "四国めたん": 2}
EMOTIONS = {"NORMAL", "HAPPY", "SERIOUS", "SURPRISED", "TROUBLED", "SKEPTICAL"}
SPEED, ATEMPO = 1.10, 1.15


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def run_command(command, *, env=None, log=None):
    if log:
        with Path(log).open("wb") as handle:
            result = subprocess.run(command, env=env, stdout=handle, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}); see {log}")
    else:
        subprocess.run(command, env=env, check=True)


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"BUSY: {path}") from None
        yield


def setup(args):
    home = Path.home()
    old = home / "ai-army-local-llm-video"
    shell_default = home / "hf-site-agent/.media-cache/assets/characters/zm_shell_20230806/extracted/Shikokumetan_Zundamon_Shell(20230806)"
    values = {
        "engine_dir": args.engine_dir or home / ".local/share/voicevox_engine/linux-cpu-x64",
        "shell": args.shell or shell_default,
        "font": args.font or old / "fonts/ZenMaruGothic-Medium.ttf",
        "background": args.background or home / "export-fix/assets/production/blackboard-user.jpg",
    }
    for key, value in values.items():
        values[key] = str(Path(value).expanduser().resolve())
        if not Path(values[key]).exists():
            raise FileNotFoundError(f"{key}: {values[key]}; specify --{key.replace('_', '-')}")
    if not (Path(values["engine_dir"]) / "run").is_file():
        raise FileNotFoundError("Specify the directory containing the VOICEVOX CPU executable 'run'")
    for character in ("Zundamon", "Metan"):
        if not (Path(values["shell"]) / character).is_dir():
            raise FileNotFoundError(f"Missing native parts: {character}")
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise FileNotFoundError(f"Install {executable}")
    from PIL import ImageFont
    ImageFont.truetype(values["font"], 36)
    atomic_json(ROOT / "runtime.local.json", values)
    print("READY: CPU VOICEVOX × 10 / approved native parts", flush=True)


def load_project(path):
    project = read_json(path)
    from renderer.production_cues import validate_direction
    validate_direction(project)
    if not isinstance(project.get("title"), str) or not project["title"].strip():
        raise ValueError("title is required")
    thumbnail_title = project.get('thumbnail_title', project['title'])
    if not isinstance(thumbnail_title, str) or not 1 <= len(thumbnail_title.strip()) <= 120:
        raise ValueError('Use thumbnail_title with 1..120 characters')
    lines = project.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("lines must be a nonempty array")
    ids = set()
    for row in lines:
        if not isinstance(row.get("id"), int) or isinstance(row["id"], bool) or row["id"] in ids:
            raise ValueError("line ids must be unique integers")
        ids.add(row["id"])
        if row.get("speaker") not in SPEAKERS or not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError(f"Invalid speaker/text in line {row['id']}")
        row.setdefault("emotion", "NORMAL")
        if row["emotion"] not in EMOTIONS:
            raise ValueError(f"Invalid emotion in line {row['id']}")
        if not isinstance(row.get("speech_text", row["text"]), str) or not row.get("speech_text", row["text"]).strip():
            raise ValueError("speech_text must be nonempty")
    visuals = project.get("visuals")
    if not isinstance(visuals, list) or not visuals:
        raise ValueError("At least one visual image is required")
    visual_ids = set()
    for visual in visuals:
        if not visual.get("id") or visual["id"] in visual_ids:
            raise ValueError("visual ids must be unique")
        visual_ids.add(visual["id"])
        if visual.get('source_language') == 'en' and (
                not isinstance(visual.get('translation'), str) or not visual['translation'].strip()):
            raise ValueError('English evidence requires a Japanese translation')
        prepared = prepare_visual(visual, path.parent, ROOT / ".cache/edited-assets")
        visual.clear()
        visual.update(prepared)
    for row in lines:
        row.setdefault("visual", visuals[0]["id"])
        if row["visual"] not in visual_ids:
            raise ValueError(f"Unknown visual in line {row['id']}")
    audio = project.get("audio", {})
    if not isinstance(audio.get('credits', []), list) or any(
            not isinstance(credit, str) for credit in audio.get('credits', [])):
        raise ValueError('audio.credits must be a list of credit strings')
    if audio.get("bgm"):
        audio["bgm"] = str((path.parent / audio["bgm"]).resolve())
        if not Path(audio["bgm"]).is_file():
            raise FileNotFoundError(audio["bgm"])
    bank = audio.get('se', {})
    def resolve_cue(cue):
        cue = {'file': cue} if isinstance(cue, str) else dict(cue)
        name = cue['file']
        cue['file'] = str((path.parent / bank.get(name, name)).resolve())
        if not Path(cue['file']).is_file():
            raise FileNotFoundError(cue['file'])
        return cue
    for row in lines:
        if row.get('se'):
            row['se'] = resolve_cue(row['se'])
    if audio.get('transition_se'):
        audio['transition_se'] = resolve_cue(audio['transition_se'])
    if audio.get('chapter_se'):
        audio['chapter_se'] = resolve_cue(audio['chapter_se'])
    from renderer.production_cues import attach_motion
    attach_motion(project, None, {}, None)
    return project


def wav_info(path):
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"Not mono PCM16: {path}")
        frames, rate = handle.getnframes(), handle.getframerate()
        if frames == 0:
            raise ValueError(f"Empty audio: {path}")
        return frames, rate


def cache_entry(row, engine_sha, code_sha):
    specification = {"text": row.get("speech_text", row["text"]),
        "speaker_id": SPEAKERS[row["speaker"]], "engine_sha256": engine_sha,
        "synth_code_sha256": code_sha, "speedScale": SPEED, "atempo": ATEMPO,
        "sample_rate": 48000, "device": "cpu"}
    directory = ROOT / ".cache/audio" / json_digest(specification)
    receipt = directory / "receipt.json"
    valid = False
    if receipt.is_file():
        data = read_json(receipt)
        valid = data.get("specification") == specification and all(
            (directory / name).is_file() and sha256(directory / name) == digest
            for name, digest in data.get("files", {}).items()) and set(data.get("files", {})) == {
                "raw.wav", "voice.wav", "query.json"}
    return directory, specification, valid


def prepare_audio(project, runtime, out, engine_sha):
    started = time.monotonic()
    code_sha = json_digest([sha256(ROOT / "pipeline.py"), sha256(ROOT / "engine_pool.py")])
    entries = [cache_entry(row, engine_sha, code_sha) for row in project["lines"]]
    # Deduplicate identical narration before assigning independent work to engines.
    unique = {}
    for row, (directory, specification, valid) in zip(project["lines"], entries):
        if not valid:
            unique.setdefault(str(directory), (row, directory, specification))
    pending = list(unique.values())
    report = {"device": "cpu", "gpu_used": False, "workers": WORKERS,
              "engine_sha256": engine_sha,
              "reused_lines": sum(valid for _, _, valid in entries), "new_unique_lines": len(pending)}
    if pending:
        print(f"CPU VOICEVOX: 10 engines / {len(pending)} new unique lines", flush=True)
        pool = EnginePool(runtime["engine_dir"], out / "engine-logs")
        try:
            with pool:
                barrier = threading.Barrier(WORKERS)

                def worker(index):
                    barrier.wait(timeout=30)
                    for row, directory, specification in pending[index::WORKERS]:
                        directory.mkdir(parents=True, exist_ok=True)
                        raw, query = pool.synthesize(index, specification["text"], specification["speaker_id"], SPEED)
                        with wave.open(io.BytesIO(raw), "rb") as handle:
                            if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or not handle.getnframes():
                                raise ValueError("Engine returned unusable WAV")
                        (directory / "raw.wav").write_bytes(raw)
                        atomic_json(directory / "query.json", query)
                        temp = directory / (uuid.uuid4().hex + ".wav")
                        run_command(["ffmpeg", "-v", "error", "-nostdin", "-i", str(directory / "raw.wav"),
                            "-af", f"atempo={ATEMPO}", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                            "-threads", "1", str(temp)])
                        wav_info(temp)
                        temp.replace(directory / "voice.wav")
                        atomic_json(directory / "receipt.json", {"specification": specification,
                            "engine_version": pool.versions[index],
                            "files": {name: sha256(directory / name) for name in ("raw.wav", "voice.wav", "query.json")}})
                with ThreadPoolExecutor(max_workers=WORKERS) as executor:
                    futures = [executor.submit(worker, index) for index in range(WORKERS)]
                    for future in futures:
                        future.result()
        finally:
            report.update(pool.report)
            atomic_json(out / "cpu-voice-receipt.json", report)
    records, frame_position = [], 0
    with wave.open(str(out / "narration.wav"), "wb") as narration:
        narration.setnchannels(1)
        narration.setsampwidth(2)
        narration.setframerate(48000)
        for row, (directory, _, _) in zip(project["lines"], entries):
            frames, rate = wav_info(directory / "voice.wav")
            if rate != 48000:
                raise ValueError("Cache audio sample rate differs")
            with wave.open(str(directory / "voice.wav"), "rb") as source:
                narration.writeframes(source.readframes(frames))
            records.append({"id": row["id"], "speaker": row["speaker"],
                "voicevox_speaker_id": SPEAKERS[row["speaker"]], "caption_text": row["text"],
                "emotion": row["emotion"], "visual_id": row["visual"], "scene_id": row.get("section", "main"),
                "voice_text": row["text"],
                "caption_emphasis_terms": row.get('caption_emphasis_terms', []),
                "start": frame_position / 48000, "end": (frame_position + frames) / 48000,
                "audio_sha256": sha256(directory / "voice.wav"), "cache": str(directory)})
            frame_position += frames
    report["audio_seconds"] = frame_position / 48000
    report["audio_phase_seconds"] = round(time.monotonic() - started, 3)
    atomic_json(out / "cpu-voice-receipt.json", report)
    atomic_json(out / "timing.json", {"records": records, "device": "cpu", "sample_rate": 48000})
    return records, report


def render(project, runtime, out, records):
    started = time.monotonic()
    profile = read_json(ROOT / "config/landscape_profile_v18.json")
    output_width, output_height = profile['output']['width'], profile['output']['height']
    profile["layout"]["background_image"] = runtime["background"]
    atomic_json(out / "profile.json", profile)
    write_subtitles(records, runtime['font'], profile['layout'], out)
    visuals = []
    for visual in project["visuals"]:
        visuals.append({**visual, "source_credit": visual.get("source_credit", "利用者提供素材"),
            "source_url": visual.get("source_url", "user-provided:local-image"),
            "source_label": visual.get("source_label", visual.get("source_credit", "利用者提供素材")),
            "media_region_only": True, "zoom_crop": 0.0})
    sections = [{"id": section, "heading": section if section != "main" else project["title"]}
                for section in dict.fromkeys(row["scene_id"] for row in records)]
    presentation = {"title": project["title"], "voice_credit": "VOICEVOX:ずんだもん・四国めたん",
        "source_credit": " / ".join(dict.fromkeys(v["source_credit"] for v in visuals)),
        "source_url": visuals[0]["source_url"], "visuals": visuals, "sections": sections}
    from renderer.production_cues import compile_direction, attach_motion, draw_direction
    presentation['production_cues'] = compile_direction(project, records)
    attach_motion(project, records, presentation, out)
    # Reject unreadable overview rows before starting the video encoder.
    from PIL import Image
    for window in presentation['production_cues']['overview']:
        draw_direction(Image.new('RGB', (1280, 720)), presentation['production_cues'],
                       window['start'], runtime['font'])
    atomic_json(out / "presentation.json", presentation)
    (out/'chapters.txt').write_text('\n'.join(
        f"{int(c['start'])//60:02d}:{int(c['start'])%60:02d} 第{c['index']}章 {c['title']}"
        for c in presentation['production_cues']['chapters'])+'\n')
    env = dict(os.environ, VIDEO_REVIEW_MODE="batch", DIRECT_ENCODE="1", OUT_CRF="20",
               OUT_VF=f"scale={output_width}:{output_height}:flags=lanczos", OUT_PRESET="veryfast", DIRECT_THREADS="4",
               CUDA_VISIBLE_DEVICES="-1")
    temporary = out / ("silent-" + uuid.uuid4().hex + ".mp4")
    print(f"RENDER: {output_width}×{output_height} / {profile['layout']['fps']}fps / veryfast / CRF20 / native expressions and mouth", flush=True)
    run_command([sys.executable, str(ROOT / "renderer/render_reusable_landscape_raw.py"),
        "--audio", str(out / "narration.wav"), "--timing", str(out / "timing.json"),
        "--presentation", str(out / "presentation.json"), "--profile", str(out / "profile.json"),
        "--shell", runtime["shell"], "--font", runtime["font"], "--visual", visuals[0]["file"],
        "--start", "0", "--duration", str(records[-1]["end"]), "--video-only",
        "--cache-root", str(ROOT / ".cache/characters"), "--output", str(temporary)],
        env=env, log=out / "render.log")
    final_temp = out / ("final-" + uuid.uuid4().hex + ".mp4")
    effects = sound_effects(project, records)
    mux_audio(temporary, out / "narration.wav", final_temp, {**project.get('audio', {}), 'effects': effects})
    make_thumbnail(final_temp, out / 'thumbnail.png', project.get('thumbnail_title', project['title']), runtime['font'])
    if (out / "video.mp4").exists():
        raise FileExistsError("Will not replace an existing video.mp4")
    final_temp.replace(out / "video.mp4")
    atomic_json(out / 'sound-effects.json', effects)
    temporary.unlink()
    temporary.with_suffix(".report.json").rename(out / "render-report.json")
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                               "-of", "json", str(out / "video.mp4")]))
    if not {"video", "audio"}.issubset({s["codec_type"] for s in probe["streams"]}):
        raise RuntimeError("Encoder output is missing video or audio")
    atomic_json(out / "media.json", probe)
    return round(time.monotonic() - started, 3), probe


def execute(args):
    started = time.monotonic()
    project_path, out = args.project.expanduser().resolve(), args.out.expanduser().resolve()
    runtime = read_json(ROOT / "runtime.local.json")
    project = load_project(project_path)
    fingerprint_started = time.monotonic()
    engine_sha = engine_fingerprint(runtime["engine_dir"], engine_fingerprint_cache(runtime["engine_dir"]))
    shell = Path(runtime["shell"])
    shell_sha = json_digest({str(p.relative_to(shell)): sha256(p) for p in sorted(shell.rglob("*.png"))})
    fingerprint_seconds = round(time.monotonic() - fingerprint_started, 3)
    implementation = {str(p.relative_to(ROOT)): sha256(p) for directory in (ROOT, ROOT / "renderer", ROOT / "config")
                      for p in directory.iterdir() if p.is_file() and p.suffix in (".py", ".json") and p.name != "runtime.local.json"}
    implementation['requirements.txt'] = sha256(ROOT / 'requirements.txt')
    asset_files = [v["file"] for v in project["visuals"]] + [v["video_file"] for v in project["visuals"] if v.get("video_file")]
    if project.get("audio", {}).get("bgm"):
        asset_files.append(project["audio"]["bgm"])
    asset_files += [r['se']['file'] for r in project['lines'] if r.get('se')]
    if project.get('audio', {}).get('transition_se'):
        asset_files.append(project['audio']['transition_se']['file'])
    if project.get('audio', {}).get('chapter_se'):
        asset_files.append(project['audio']['chapter_se']['file'])
    input_key = json_digest({"project": project, "assets": {p: sha256(p) for p in asset_files},
        "font": sha256(runtime["font"]), "background": sha256(runtime["background"]),
        "engine": engine_sha, "shell": shell_sha, "implementation": implementation})
    out.mkdir(parents=True, exist_ok=True)
    with lock(ROOT / ".cache/run.lock"), lock(out / ".lock"):
        state_path = out / "state.json"
        if state_path.exists():
            previous = read_json(state_path)
            if previous.get("input_key") != input_key:
                raise ValueError("Output folder belongs to different inputs; use a new --out")
            if previous.get("status") == "COMPLETE":
                if sha256(out / "video.mp4") != previous["video_sha256"]:
                    raise ValueError("Completed video has changed")
                print(json.dumps({"status": "REUSED", "video": str(out / "video.mp4")}, ensure_ascii=False))
                return
        if (out / "video.mp4").exists():
            raise FileExistsError("Will not replace existing video.mp4")
        state = {"input_key": input_key, "status": "AUDIO", "device": "cpu", "workers": WORKERS}
        atomic_json(state_path, state)
        try:
            records, audio_report = prepare_audio(project, runtime, out, engine_sha)
            state["status"] = "RENDER"
            atomic_json(state_path, state)
            render_seconds, probe = render(project, runtime, out, records)
            (out / "script.txt").write_text("\n".join(f"{r['speaker']}：{r['text']}" for r in project["lines"]) + "\n")
            (out / "description.txt").write_text(project["title"] + "\n\nVOICEVOX:ずんだもん\nVOICEVOX:四国めたん\n\n" +
                "\n".join(f"{v.get('source_credit', '利用者提供素材')} {v.get('source_url', '')}" for v in project["visuals"]) + "\n")
            with (out/'description.txt').open('a') as description:
                chapters = (out/'chapters.txt').read_text().strip()
                if chapters:
                    description.write('\n章立て\n'+chapters+'\n')
                for credit in project.get('audio', {}).get('credits', []):
                    description.write('\n'+str(credit)+'\n')
            state.update(status="COMPLETE", video=str(out / "video.mp4"), video_sha256=sha256(out / "video.mp4"),
                bytes=(out / "video.mp4").stat().st_size, duration=float(probe["format"]["duration"]),
                total_seconds=round(time.monotonic() - started, 3), render_seconds=render_seconds,
                fingerprint_seconds=fingerprint_seconds,
                audio_phase_seconds=audio_report["audio_phase_seconds"],
                max_parallel_synthesis=audio_report.get("max_parallel_synthesis", 0), gpu_used=False)
            atomic_json(state_path, state)
            print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
        except BaseException as error:
            state.update(status="FAILED", error=f"{type(error).__name__}: {error}")
            atomic_json(state_path, state)
            raise


def main():
    parser = argparse.ArgumentParser(description="ずんだもん動画：VOICEVOX CPU10並列 → MP4（GPUなし）")
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup", help="既存CPUエンジンと素材を登録（初回だけ）")
    for name in ("engine-dir", "shell", "font", "background"):
        setup_parser.add_argument("--" + name, type=Path)
    for name in ("run", "_run"):
        command = commands.add_parser(name, help="台本JSONと画像から動画を完成")
        command.add_argument("project", type=Path)
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "setup":
        setup(args)
    elif args.command == "run":
        shared = Path.home() / ".local/bin/video-ops"
        if shared.is_file():
            return subprocess.call([str(shared), "run", "--", sys.executable, str(Path(__file__).resolve()),
                                    "_run", str(args.project.resolve()), "--out", str(args.out.resolve())])
        with lock(Path.home() / ".cache/video-tools/ffmpeg.lock"):
            execute(args)
    else:
        execute(args)
    return 0


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(f"STOP: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
