"""Run a project on a Colab NVIDIA runtime; stop if GPU encoding is unavailable."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.install_voicevox_gpu import install


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--engine-dir', type=Path, required=True)
    parser.add_argument('--shell', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--background', type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('COLAB_RELEASE_TAG') and not os.environ.get('COLAB_GPU'):
        raise RuntimeError('This entry point requires a Google Colab runtime')
    if args.out.exists():
        raise FileExistsError('Use a new output directory')
    if (ROOT/'runtime.local.json').exists():
        raise FileExistsError('Use a fresh private checkout without runtime.local.json')
    for path in (args.project, args.shell, args.font, args.background):
        if not path.exists():
            raise FileNotFoundError(path)
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'], text=True).strip()
    if not gpu:
        raise RuntimeError('No NVIDIA GPU is available')
    # A compiled-in encoder alone does not prove the runtime permits NVENC.
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=640x360:rate=1',
                    '-frames:v', '1', '-c:v', 'h264_nvenc', '-f', 'null', '-'], check=True)
    start = time.monotonic()
    engine = install(args.engine_dir)
    env = dict(os.environ, VOICEBOX_DEVICE='cuda', PYTHONUTF8='1')
    env.pop('CUDA_VISIBLE_DEVICES', None)
    env.pop('HIP_VISIBLE_DEVICES', None)
    subprocess.run([sys.executable, str(ROOT/'pipeline.py'), 'setup', '--engine-dir', str(engine),
                    '--shell', str(args.shell), '--font', str(args.font), '--background', str(args.background)], env=env, check=True)
    subprocess.run([sys.executable, str(ROOT/'pipeline.py'), 'run', str(args.project), '--out', str(args.out)], env=env, check=True)
    state = json.loads((args.out/'state.json').read_text(encoding='utf-8'))
    if state.get('status') != 'COMPLETE' or not state.get('gpu_used'):
        raise RuntimeError('GPU synthesis was not confirmed')
    (args.out/'colab-gpu-receipt.json').write_text(json.dumps({
        'runtime': 'Google Colab', 'gpu_and_driver': gpu, 'audio': 'VOICEVOX --use_gpu',
        'video_encoder': 'h264_nvenc', 'frame_composition': 'CPU',
        'seconds_including_install': round(time.monotonic()-start, 3),
        'video_sha256': state['video_sha256']}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
