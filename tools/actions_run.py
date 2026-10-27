"""Actions entry: transient ZIP production or a generated CPU/editing exercise."""
from pathlib import Path
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline
from media_editing import edit_sequence, mux_audio, prepare_visual, make_thumbnail

FONT_COMMIT = "ef3b6f8df2d299a7a82c68cd8743c8f9c11e1c8f"
FONT_URL = f"https://raw.githubusercontent.com/google/fonts/{FONT_COMMIT}/ofl/zenmarugothic/ZenMaruGothic-Medium.ttf"
FONT_SHA256 = "3cfdb98a13571ede17fcc769f5093a97c38b80a7b9b2ab754a26b4d822092b3b"


def download(url, path, maximum=2 * 1024**3):
    if not url.startswith("https://"):
        raise ValueError("Input ZIP must use HTTPS")
    count = 0
    with urllib.request.urlopen(url, timeout=180) as response, Path(path).open("wb") as output:
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > maximum:
                raise ValueError("Input exceeds the size limit")
            output.write(block)


def extract_project(archive, destination):
    destination = destination.resolve()
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist()) > 20000:
            raise ValueError("Too many ZIP entries")
        for entry in bundle.infolist():
            path = destination / entry.filename
            total += entry.file_size
            if not path.resolve().is_relative_to(destination) or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("ZIP paths must remain inside the project")
            if total > 4 * 1024**3:
                raise ValueError("Expanded input exceeds 4 GiB")
        bundle.extractall(destination)
    projects = list(destination.rglob("project.json")) + list(destination.rglob('research-request.json'))
    if len(projects) != 1:
        raise ValueError("ZIP must contain exactly one project.json or research-request.json")
    return projects[0]


def install_font(directory):
    path = directory / "ZenMaruGothic-Medium.ttf"
    if not path.is_file() or pipeline.sha256(path) != FONT_SHA256:
        download(FONT_URL, path, maximum=16 * 1024**2)
        if pipeline.sha256(path) != FONT_SHA256:
            raise ValueError("Official Zen Maru font SHA-256 mismatch")
    fonts = Path.home() / ".local/share/fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, fonts / path.name)
    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f", str(fonts)], check=True, stdout=subprocess.DEVNULL)
    return path


def production(project_path, engine, font, output, assets_root=None):
    assets_root = assets_root or project_path.parent
    configuration = assets_root / "runtime-assets.json"
    runtime = json.loads(configuration.read_text())
    for key in ("shell", "background"):
        path = (assets_root / runtime[key]).resolve()
        if not path.is_relative_to(assets_root.resolve()):
            raise ValueError("Runtime assets must be inside the input project")
        runtime[key] = str(path)
    pipeline.setup(argparse.Namespace(engine_dir=engine, shell=Path(runtime["shell"]),
                                      background=Path(runtime["background"]), font=font))
    pipeline.execute(argparse.Namespace(project=project_path, out=output))
    state = pipeline.read_json(output / "state.json")
    return {"mode": "production", **state}


def demo(engine, font, working, output):
    """A generated functional fixture, not a committed production script or native-character fallback."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from moviepy import ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
    profile = pipeline.read_json(ROOT / 'config/landscape_profile_v18.json')
    fps = profile['layout']['fps']
    output.mkdir(parents=True, exist_ok=True)
    source = working / "diagram.svg"
    boxes = ''.join(f'<rect x="{40+i*116}" y="170" width="100" height="130" rx="14" fill="#bfe8d0"/>'
                    f'<text x="{90+i*116}" y="245" text-anchor="middle" font-size="40">{i+1}</text>' for i in range(10))
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
        '<rect width="1280" height="720" fill="#edf8f1"/>'
        '<g font-family="Zen Maru Gothic" fill="#204a32">'
        '<text x="640" y="85" text-anchor="middle" font-size="46">VOICEVOX CPU × 10 / GitHub Actions</text>'
        + boxes + '<text x="640" y="370" text-anchor="middle" font-size="32">MoviePy + CairoSVG + FFmpeg</text></g></svg>')
    visual = prepare_visual({"id": "diagram", "file": str(source)}, working, ROOT / ".cache/edited-assets")
    # Fixture sentences are constructed here in runner temporary storage; no production script is committed.
    lines = [{"id": i+1, "speaker": "ずんだもん" if i % 2 == 0 else "四国めたん",
              "text": f"シーピーユー音声、担当{ i+1 }の合成です。", "emotion": "NORMAL", "visual": "diagram"}
             for i in range(10)]
    project = {"title": "VOICEVOX CPU10並列 動作デモ", "lines": lines, "visuals": [visual]}
    pipeline.atomic_json(working / "project.json", project)
    records, audio_report = pipeline.prepare_audio(project, {"engine_dir": str(engine)}, output,
        pipeline.engine_fingerprint(engine, pipeline.engine_fingerprint_cache(engine)))
    duration = records[-1]["end"]
    source_clips = []
    for index, color in enumerate(((70, 150, 110), (75, 120, 185))):
        path = working / f"source-{index}.mp4"
        with ColorClip((640, 300), color=color, duration=3) as background:
            with ColorClip((100, 100), color=(245, 220, 120), duration=3) as marker:
                with CompositeVideoClip([background, marker.with_position(lambda t:(30+t*130,100))]) as clip:
                    clip.write_videofile(str(path), fps=30, codec="libx264", audio=False,
                        preset="veryfast", threads=2, ffmpeg_params=["-crf", "18"], logger=None)
        source_clips.append({"file": str(path), "start": 0.25, "end": 2.75, "crop": [0, 0, 640, 300]})
    edited = working / "edited.mp4"
    edit_sequence(source_clips, edited, transition=0.35)
    caption_clips = []
    for row in records:
        image = Image.new("RGBA", (1280, 160))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((100, 2, 1180, 154), 18,
            fill="#e2f3e8" if row["speaker"] == "ずんだもん" else "#fae7ef", outline="#57886a", width=3)
        draw.text((130, 17), row["speaker"], font=ImageFont.truetype(str(font), 26), fill="#254632")
        draw.text((130, 66), row["caption_text"], font=ImageFont.truetype(str(font), 36), fill="#254632")
        caption_clips.append(ImageClip(np.array(image)).with_start(row["start"]).with_duration(row["end"]-row["start"]).with_position((0, 540)))
    silent = working / "silent.mp4"
    with ImageClip(visual["file"]).resized((1280, 720)).with_duration(duration) as background:
        with VideoFileClip(str(edited), audio=False) as clip:
            with CompositeVideoClip([background, clip.resized(width=650).with_position((315, 210)).with_start(1), *caption_clips]) as composition:
                composition.with_duration(duration).write_videofile(str(silent), fps=fps, codec="libx264", audio=False,
                    preset="veryfast", threads=2, ffmpeg_params=["-vf", f"scale={profile['output']['width']}:{profile['output']['height']}:flags=lanczos",
                        "-crf", "20", "-pix_fmt", "yuv420p"], logger=None)
    for clip in caption_clips:
        clip.close()
    bgm = working / "test-tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-t", str(duration), "-c:a", "pcm_s16le", str(bgm)], check=True)
    mux_audio(silent, output / "narration.wav", output / "video.mp4", {"bgm": str(bgm), "bgm_gain_db": -30, "ducking": True})
    make_thumbnail(output / 'video.mp4', output / 'thumbnail.png', project['title'], font)
    media = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output / "video.mp4")]))
    if audio_report.get("max_parallel_synthesis", 0) < 2:
        raise RuntimeError("CPU speech requests did not overlap")
    return {"mode": "cpu-and-editing-demo", "duration": float(media["format"]["duration"]),
            "voice_workers": 10, "max_parallel_synthesis": audio_report["max_parallel_synthesis"],
            "gpu_used": False, "owned_engines_stopped": audio_report["owned_engines_stopped"],
            "engine_ready_seconds": audio_report["engine_ready_seconds"],
            "audio_phase_seconds": audio_report["audio_phase_seconds"],
            "tools_exercised": ["VOICEVOX", "MoviePy trim/crop/crossfade", "CairoSVG", "FFmpeg BGM ducking"],
            "native_characters": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "production"], default="demo")
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    font = install_font(args.engine_dir.parent)
    with tempfile.TemporaryDirectory(prefix="voicebox-project-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
        working = Path(temporary)
        if args.mode == "production":
            url = os.environ.get("PROJECT_ZIP_URL", "")
            if not url:
                raise ValueError("production mode needs a project ZIP URL")
            archive = working / "input.zip"
            download(url, archive)
            project = extract_project(archive, working / "project")
            assets_root = project.parent
            if project.name == 'research-request.json':
                from tools.prepare_project import prepare
                request = json.loads(project.read_text())
                selected = os.environ.get('SCRIPT_PROVIDER', 'request')
                if (selected == 'opencode') != (request.get('provider') == 'opencode'):
                    raise ValueError('OpenCode requires matching script_provider and research-request.json provider')
                prepared = working / 'prepared-project'
                try:
                    prepare(request, prepared, allow_api=os.environ.get('ALLOW_SCRIPT_API') == 'true',
                            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'))
                finally:
                    if prepared.exists():
                        shutil.copytree(prepared, output / 'source-pack')
                project = prepared / 'project.json'
                if not project.is_file():
                    raise ValueError('Research finished; production needs a supplied script or an explicitly enabled script API')
            result = production(project, args.engine_dir, font, output, assets_root=assets_root)
        else:
            result = demo(args.engine_dir, font, working, output)
    result.update(wall_seconds=round(time.monotonic()-started, 3), video_sha256=pipeline.sha256(output / "video.mp4"),
                  runner=os.environ.get("RUNNER_NAME", "local"), cpu_count=os.cpu_count())
    pipeline.atomic_json(output / "metrics.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as stream:
            stream.write("## VOICEVOX CPU / video result\n\n```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()
