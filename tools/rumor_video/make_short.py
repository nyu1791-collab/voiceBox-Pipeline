"""本編の MP4 の冒頭から、縦型ショート（1080x1920）を作る。
上：見出し　中：本編の映像（16:9）　下：要点の枠（非公式の表示つき）。
使い方:
  python tools/rumor_video/make_short.py --video 本編.mp4 --out ショート.mp4 --font フォント.ttf \
      --head 見出し.txt --points 要点.txt [--seconds 58]
文字は textfile で渡すので、日本語の引用符や改行で壊れない。"""
import argparse
import subprocess
from pathlib import Path


def build_command(video, out, font, head, points, seconds=58):
    filter_graph = (
        "[0:v]scale=1080:607,setsar=1[v];"
        f"color=c=0x2B6146:s=1080x1920:d={seconds}[bg];"
        "[bg][v]overlay=0:420[bgv];"
        f"[bgv]drawtext=fontfile={font}:textfile={head}:fontcolor=white:fontsize=68:x=(w-text_w)/2:y=110:line_spacing=18,"
        "drawbox=x=40:y=1090:w=1000:h=760:color=white@0.92:t=fill,"
        f"drawtext=fontfile={font}:textfile={points}:fontcolor=0x2B6146:fontsize=36:x=80:y=1140:line_spacing=26[out]"
    )
    return [
        "ffmpeg", "-v", "error", "-y", "-i", str(video), "-t", str(seconds),
        "-filter_complex", filter_graph, "-map", "[out]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", "-movflags", "+faststart", str(out),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--head", required=True, type=Path)
    parser.add_argument("--points", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=58)
    args = parser.parse_args()
    # ffmpeg のフィルタ内で扱いやすいように、動画と同じフォルダへ相対名で置く
    cwd = args.video.parent
    for source in (args.font, args.head, args.points):
        target = cwd / source.name
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
    command = build_command(args.video.name, args.out.resolve(), args.font.name, args.head.name, args.points.name, args.seconds)
    subprocess.run(command, cwd=cwd, check=True)
    print(f"short: {args.out}")


if __name__ == "__main__":
    main()
