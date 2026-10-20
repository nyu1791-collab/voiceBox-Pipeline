from pathlib import Path
import json
import subprocess
import sys
import zipfile

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media_editing import VideoPanels, edit_sequence, mux_audio, prepare_visual, rasterize_svg
from tools.actions_run import extract_project


def test_svg_render_and_external_reference_rejection(tmp_path):
    source = tmp_path / "diagram.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="red"/></svg>')
    result = prepare_visual({"id": "svg", "file": source.name}, tmp_path, tmp_path / "cache")
    with Image.open(result["file"]) as image:
        assert image.size == (1660, 830)
        assert image.getpixel((500, 200))[:3] == (255, 0, 0)
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><image href="file:///etc/passwd" width="100" height="50"/></svg>')
    with pytest.raises(ValueError, match="self-contained"):
        rasterize_svg(source, tmp_path / "rejected.png")


def test_actual_trim_crossfade_and_video_frame_progression(tmp_path):
    from moviepy import ColorClip, VideoFileClip
    segments = []
    for name, color in [("red", (255, 0, 0)), ("blue", (0, 0, 255))]:
        path = tmp_path / (name + ".mp4")
        with ColorClip((320, 180), color=color, duration=1.5) as clip:
            clip.write_videofile(str(path), fps=30, codec="libx264", audio=False, logger=None, threads=1)
        segments.append({"file": str(path), "start": 0.25, "end": 1.25})
    output = tmp_path / "transition.mp4"
    edit_sequence(segments, output, transition=0.2)
    with VideoFileClip(str(output), audio=False) as clip:
        assert abs(clip.duration - 1.8) < 0.04
        first = clip.get_frame(0.1)[360, 640]
        middle = clip.get_frame(0.9)[360, 640]
        last = clip.get_frame(1.7)[360, 640]
        assert first[0] > 220 and first[2] < 30
        assert middle[0] > 40 and middle[2] > 40
        assert last[2] > 220 and last[0] < 30
    reader = VideoPanels([{"id": "video", "video_file": str(output)}])
    try:
        assert not np.array_equal(np.asarray(reader.frame("video", 0.1)), np.asarray(reader.frame("video", 1.7)))
    finally:
        reader.close()
    assert reader.clip is None
    prepared = prepare_visual({"id": "trimmed", "file": str(output), "edit": {"start": 0.25, "end": 1.25}},
                              tmp_path, tmp_path / "prepared")
    with VideoFileClip(prepared["video_file"], audio=False) as clip:
        assert abs(clip.duration - 1) < 0.04
    assert Path(prepared["file"]).is_file()


def test_bgm_ducking_preserves_video_and_48khz_audio(tmp_path):
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=320x180:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    audio = []
    for frequency in (440, 220):
        path = tmp_path / f"{frequency}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1",
                        "-c:a", "pcm_s16le", str(path)], check=True)
        audio.append(path)
    output = tmp_path / "mixed.mp4"
    mux_audio(silent, audio[0], output, {"bgm": str(audio[1]), "ducking": True})
    streams = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))["streams"]
    assert {stream["codec_type"] for stream in streams} == {"video", "audio"}
    sound = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert sound["sample_rate"] == "48000" and sound["channels"] == 1


def test_project_zip_stays_inside_temporary_directory(tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../outside.txt", "not permitted")
        output.writestr("project.json", "{}")
    with pytest.raises(ValueError, match="inside"):
        extract_project(archive, tmp_path / "project")
    assert not (tmp_path / "outside.txt").exists()
