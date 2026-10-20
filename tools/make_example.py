"""Create two original schematic images and a ten-line CPU smoke input."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def main():
    EXAMPLES.mkdir(exist_ok=True)
    runtime = json.loads((ROOT / "runtime.local.json").read_text())
    font = runtime["font"]
    for name, title in [("parallel", "VOICEVOX  CPU × 10"), ("flow", "台本 → 音声 → 動画")]:
        image = Image.new("RGB", (1200, 420), "#F5FAF7")
        draw = ImageDraw.Draw(image)
        draw.text((600, 45), title, font=ImageFont.truetype(font, 40), fill="#244B3A", anchor="mm")
        if name == "parallel":
            for i in range(10):
                x = 30 + i * 116
                draw.rounded_rectangle((x, 140, x + 100, 280), 14, fill="#D6F0E2", outline="#3A8760", width=3)
                draw.text((x + 50, 192), str(i + 1), font=ImageFont.truetype(font, 36), fill="#244B3A", anchor="mm")
                draw.text((x + 50, 246), "CPU", font=ImageFont.truetype(font, 22), fill="#244B3A", anchor="mm")
            draw.text((600, 360), "10エンジン・各1スレッド / GPUは使わない", font=ImageFont.truetype(font, 28), fill="#244B3A", anchor="mm")
        else:
            for i, label in enumerate(("台本JSON", "CPU音声", "MP4動画")):
                x = 40 + i * 400
                draw.rounded_rectangle((x, 130, x + 320, 290), 20, fill="#D6F0E2", outline="#3A8760", width=3)
                draw.text((x + 160, 210), label, font=ImageFont.truetype(font, 36), fill="#244B3A", anchor="mm")
                if i < 2:
                    draw.line((x + 330, 210, x + 385, 210), fill="#3A8760", width=5)
                    draw.polygon([(x + 385, 210), (x + 369, 198), (x + 369, 222)], fill="#3A8760")
        image.save(EXAMPLES / (name + ".png"))
    texts = [
        "ずんだもんと、めたんの動画を作るのだ。",
        "今回は、すべてシーピーユーで動かすのね。",
        "音声エンジンは、十個を並列に使うのだ。",
        "それぞれ別の台詞を、担当する仕組みよ。",
        "完成した順番ではなく、台本の順に並べるのだ。",
        "字幕の時刻は、実際の音声から決めるわ。",
        "表情と口の動きも、音声に合わせるのだ。",
        "文字は丸い字体で、三行までに収めるのね。",
        "同じ音声は保存して、次回も使うのだ。",
        "これで動画の完成よ。早速、使ってみましょう。",
    ]
    emotions = ["NORMAL", "NORMAL", "HAPPY", "HAPPY", "SERIOUS", "SERIOUS", "SURPRISED", "TROUBLED", "HAPPY", "NORMAL"]
    project = {"title": "ずんだもん動画・CPU10並列", "visuals": [
        {"id": name, "file": name + ".png", "source_credit": "このリポジトリの説明図", "source_label": "構成を示す模式図"}
        for name in ("parallel", "flow")], "lines": [
            {"id": i + 1, "speaker": "ずんだもん" if i % 2 == 0 else "四国めたん",
             "text": text, "emotion": emotions[i], "visual": "parallel" if i < 5 else "flow"}
            for i, text in enumerate(texts)]}
    (EXAMPLES / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
