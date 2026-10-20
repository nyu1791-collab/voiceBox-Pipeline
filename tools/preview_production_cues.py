"""Preview chapter/diagram/SE behavior without rewriting a script or starting TTS."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from renderer.production_cues import compile_direction, attach_motion, draw_direction
from renderer.motion_diagrams import active_diagram, render_panel
from media_editing import mux_audio, sound_effects


def preview(project, records, output, font):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output/'preview.mp4').exists():
        raise FileExistsError('Use a new preview output')
    cues = compile_direction(project, records)
    presentation = {}
    attach_motion(project, records, presentation, output)
    timeline = presentation.get('motion_diagrams', {})
    duration, fps = records[-1]['end'], 24
    silent = output/'silent.mp4'
    command = ['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '1280x720',
               '-r', str(fps), '-i', 'pipe:0', '-vf', 'scale=640:360:flags=lanczos', '-an',
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', str(silent)]
    face = lambda size: ImageFont.truetype(str(font), size)
    with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
        try:
            for i in range(round(duration*fps)):
                t = i/fps
                frame = Image.new('RGB', (1280,720), '#E9EEF5')
                draw = ImageDraw.Draw(frame)
                draw.text((250, 190), '制作時間の実測を使った表示例', font=face(30), fill='#245B43')
                diagram = active_diagram(timeline, t)
                if diagram:
                    frame.paste(render_panel(diagram, t, (830,390), font, 1), (225,92))
                draw_direction(frame, cues, t, font)
                draw = ImageDraw.Draw(frame)
                row = next((r for r in records if r['start'] <= t < r['end']), None)
                if row:
                    draw.rounded_rectangle((205,498,1075,666), radius=12, fill='white')
                    draw.text((225,510), ('質問：' if row['id'] in (2,5) else '解説：')+
                              ('ずんだもん' if row['id'] in (2,5) else '四国めたん'), font=face(25), fill='#245B43')
                    text = row['caption_text']
                    for n in range(0,len(text),23):
                        draw.text((225,550+(n//23)*43), text[n:n+23], font=face(32), fill='#26332D')
                draw.text((30,688), '演出デモ／表示・SEの機能確認用。キャラクター素材・合成音声なし。', font=face(20), fill='#245B43')
                if i in (12,84,132): frame.save(output/f'frame-{i}.png')
                encoder.stdin.write(frame.tobytes())
            encoder.stdin.close()
            if encoder.wait(): raise RuntimeError('Preview encoder failed')
        finally:
            if encoder.poll() is None:
                encoder.terminate(); encoder.wait()
    narration = output/'silence.wav'
    with wave.open(str(narration), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
        wav.writeframes(b'\0\0'*round(duration*48000))
    effects = sound_effects(project, records)
    mux_audio(silent, narration, output/'preview.mp4', {'effects': effects})
    (output/'effects.json').write_text(json.dumps(effects,ensure_ascii=False,indent=2)+'\n')
    (output/'preview-scope.json').write_text(json.dumps({
        'scope':'Feature demonstration only; authored timing, no synthesized speech or character assets',
        'width':640,'height':360,'fps':fps,'seconds':duration,'effects':len(effects)},indent=2)+'\n')
    silent.unlink(); narration.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--timing', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args()
    preview(json.loads(args.project.read_text()), json.loads(args.timing.read_text())['records'],
            args.output, args.font)
