"""MoviePy clips, CairoSVG diagrams, and FFmpeg narration/BGM finishing."""
from contextlib import ExitStack
from pathlib import Path
import hashlib
import json
import math
import os
import shutil
import subprocess
import uuid

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
os.environ.setdefault("IMAGEIO_FFMPEG_EXE", shutil.which("ffmpeg") or "ffmpeg")


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rasterize_svg(source, destination):
    from cairosvg.surface import PNGSurface

    def no_external_resource(url, resource_type):
        raise ValueError("Use self-contained SVG; external resources are not fetched")

    PNGSurface.convert(bytestring=Path(source).read_bytes(), write_to=str(destination),
                       output_width=1660, url_fetcher=no_external_resource)


def edit_sequence(segments, destination, transition=0.25):
    from moviepy import CompositeVideoClip, VideoFileClip, concatenate_videoclips, vfx
    if not segments:
        raise ValueError("A clip sequence cannot be empty")
    transition = float(transition)
    if not math.isfinite(transition) or transition < 0:
        raise ValueError("transition must be a nonnegative finite number")
    with ExitStack() as resources:
        clips = []
        for item in segments:
            source = resources.enter_context(VideoFileClip(str(item["file"]), audio=False))
            begin = float(item.get("start", 0))
            end = float(item.get("end", source.duration))
            if not 0 <= begin < end <= source.duration + 0.001:
                raise ValueError("Clip trim is outside its source duration")
            clip = source.subclipped(begin, min(end, source.duration)).without_audio()
            crop = item.get("crop")
            if crop is not None:
                x1, y1, x2, y2 = map(int, crop)
                if not 0 <= x1 < x2 <= source.w or not 0 <= y1 < y2 <= source.h:
                    raise ValueError("Crop is outside the source frame")
                clip = clip.with_effects([vfx.Crop(x1=x1, y1=y1, x2=x2, y2=y2)])
            ratio = min(1280 / clip.w, 720 / clip.h)
            clip = clip.resized((max(2, round(clip.w * ratio)), max(2, round(clip.h * ratio))))
            canvas = resources.enter_context(CompositeVideoClip(
                [clip.with_position("center")], size=(1280, 720), bg_color=(255, 255, 255)))
            canvas = canvas.with_duration(clip.duration)
            if len(segments) > 1 and transition >= canvas.duration / 2:
                raise ValueError("Transition must be shorter than half of every clip")
            if clips and transition:
                canvas = canvas.with_effects([vfx.CrossFadeIn(transition)])
            clips.append(canvas)
        joined = resources.enter_context(concatenate_videoclips(clips, method="compose", padding=-transition))
        joined.write_videofile(str(destination), fps=30, codec="libx264", audio=False,
            preset="medium", threads=2, ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"], logger=None)


def prepare_visual(visual, base, cache):
    visual = dict(visual)
    segments = visual.get("clips")
    source = (base / visual["file"]).resolve() if visual.get("file") else None
    if not segments and (source is None or not source.is_file()):
        raise FileNotFoundError(source)
    if segments:
        segments = [{**item, "file": str((base / item["file"]).resolve())} for item in segments]
    elif source.suffix.lower() in VIDEO_SUFFIXES:
        segments = [{**visual.get("edit", {}), "file": str(source)}]
    elif source.suffix.lower() != ".svg":
        visual["file"] = str(source)
        return visual
    sources = [Path(item["file"]) for item in segments] if segments else [source]
    specification = {"sources": {str(p): digest_file(p) for p in sources},
                     "segments": segments, "transition": visual.get("transition", 0.25),
                     "implementation": digest_file(__file__)}
    key = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()
    folder = Path(cache) / key
    folder.mkdir(parents=True, exist_ok=True)
    poster, video = folder / "poster.png", folder / "clip.mp4"
    receipt = folder / "receipt.json"
    valid = False
    if receipt.is_file():
        saved = json.loads(receipt.read_text())
        valid = saved.get("specification") == specification and all(
            (folder / name).is_file() and digest_file(folder / name) == sha
            for name, sha in saved.get("files", {}).items()) and set(saved.get("files", {})) == (
                {"poster.png", "clip.mp4"} if segments else {"poster.png"})
    if not valid:
        if segments:
            from moviepy import VideoFileClip
            temporary = folder / (uuid.uuid4().hex + ".mp4")
            edit_sequence(segments, temporary, visual.get("transition", 0.25) if len(segments) > 1 else 0)
            temporary.replace(video)
            with VideoFileClip(str(video), audio=False) as clip:
                clip.save_frame(str(poster), t=0)
        else:
            rasterize_svg(source, poster)
        names = ["poster.png", "clip.mp4"] if segments else ["poster.png"]
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps({"specification": specification,
            "files": {name: digest_file(folder / name) for name in names}}, indent=2))
        temporary.replace(receipt)
    visual.update(file=str(poster), prepared_asset_key=key)
    if segments:
        visual["video_file"] = str(video)
    return visual


def mux_audio(silent_video, narration, destination, settings=None):
    settings = settings or {}
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(silent_video), "-i", str(narration)]
    normalize = settings.get("normalize", False)
    finish = ("loudnorm=I=-17:TP=-1.5:LRA=9," if normalize else "") + "alimiter=limit=0.78:level=disabled"
    if settings.get("bgm"):
        gain = float(settings.get("bgm_gain_db", -24))
        if not math.isfinite(gain) or not -60 <= gain <= -12:
            raise ValueError("bgm_gain_db must be between -60 and -12")
        command += ["-stream_loop", "-1", "-i", str(settings["bgm"])]
        graph = "[1:a]volume=6.5dB,asplit=2[voice][side];" + f"[2:a]volume={gain}dB[bed];"
        if settings.get("ducking", True):
            graph += "[bed][side]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=250[music];"
        else:
            graph += "[side]anullsink;[bed]anull[music];"
        graph += "[voice][music]amix=inputs=2:duration=first:normalize=0," + finish + "[audio]"
        command += ["-filter_complex", graph, "-map", "0:v:0", "-map", "[audio]"]
    else:
        command += ["-map", "0:v:0", "-map", "1:a:0", "-af", "volume=6.5dB," + finish]
    command += ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
                "-movflags", "+faststart", "-shortest", str(destination)]
    subprocess.run(command, check=True)


class VideoPanels:
    """One active MoviePy decoder; frame identity is part of the renderer cache key."""
    def __init__(self, visuals):
        self.paths = {v["id"]: v["video_file"] for v in visuals if v.get("video_file")}
        self.active = None
        self.clip = None

    def frame(self, visual, elapsed):
        if visual not in self.paths:
            return None
        from moviepy import VideoFileClip
        from PIL import Image
        if self.active != visual:
            self.close()
            self.clip = VideoFileClip(self.paths[visual], audio=False)
            self.active = visual
        t = min(max(0, elapsed), max(0, self.clip.duration - 1 / max(1, self.clip.fps)))
        return Image.fromarray(self.clip.get_frame(t)).convert("RGBA")

    def close(self):
        if self.clip is not None:
            self.clip.close()
        self.clip = None
        self.active = None
