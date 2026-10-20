"""Remotion/PowerPointの無音図解を、実音声に結び付けて既存描画へ渡す。"""
from pathlib import Path
import argparse
import fcntl
import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import platform
from fractions import Fraction
from pipeline_common import atomic_json, sha256

BACKENDS = ('pillow', 'remotion', 'powerpoint')
HERE = Path(__file__).resolve().parent
RUNTIME = Path('/home/n_yu1791/.local/share/vm-plugins/remotion-runtime')
NODE = Path('/home/n_yu1791/.nvm/versions/node/v22.23.3/bin/node')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def inside(root, relative):
    p = (root / relative).resolve()
    if Path(relative).is_absolute() or not p.is_relative_to(root.resolve()):
        raise ValueError('図解素材のパスが保存領域外')
    return p


def requests_for(inputs, timeline, profile):
    if not any(d.get('backend', 'pillow') != 'pillow' for d in timeline['diagrams']):
        return []
    layout = profile['layout']; x1, y1, x2, y2 = layout['zones']['evidence']
    width, height, fps = x2-x1, y2-y1, layout['fps']
    if fps != 30 or width < 400 or height < 250 or width > 1920 or height > 1080:
        raise ValueError('図解素材の解像度またはfpsが未対応')
    binding = {n: sha256(inputs/n) for n in
               ('audio.wav', 'timing.json', 'motion-plan.json', 'motion-source.json')}
    result = []
    for d in timeline['diagrams']:
        backend = d.get('backend', 'pillow')
        if backend == 'pillow':
            continue
        if backend not in BACKENDS:
            raise ValueError('未対応の図解backend')
        frames = math.ceil((d['end']-d['start'])*fps-1e-8)
        if not 1 <= frames <= 3600:
            raise ValueError('外部図解は1〜3600フレーム')
        request = {'schema': 'motion-render-request-v1', 'backend': backend,
                   'diagram': d, 'width': width, 'height': height, 'fps': fps,
                   'frames': frames, 'supersample': 4, 'bindings': binding, 'profile_sha256': digest(profile)}
        from motion_diagrams import _fit
        font = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
        tf, title = _fit(d['title'], font, width-32, 36, 25)
        row_h = (height-83)/len(d['elements']); labels = []
        for e in d['elements']:
            f, lines = _fit(e['label'], font, width-(36 if d['template']=='bars' else 70),
                            row_h-7-(28 if d['template']=='bars' else 10),
                            21 if d['template']=='bars' else 24)
            labels.append({'font_size': f.size, 'lines': lines})
        ff, footer = _fit('説明図／出典：'+'・'.join(d['source_hosts']), font, width-28, 31, 18)
        request['typography'] = {'title': title, 'title_font': tf.size, 'labels': labels,
                                 'footer': footer, 'footer_font': ff.size}
        request['request_sha256'] = digest(request)
        result.append(request)
    return result


def version_requests(version, profile):
    from motion_diagrams import bound_timeline
    inputs = version/'inputs'
    timeline = bound_timeline(read(inputs/'presentation.json'), read(inputs/'timing.json')['records'],
                              inputs, require_assets=False)
    if timeline is None:
        return []
    return requests_for(inputs, timeline, profile)


def prepare(version, profile, out):
    requests = version_requests(version, profile)
    if not requests:
        raise ValueError('外部backendを指定した図解がない')
    out.mkdir(parents=True, exist_ok=False)
    for req in requests:
        atomic_json(out/(req['diagram']['id']+'.json'), req)
    return {'state': 'WAIT', 'reason': 'motion_assets_required',
            'requests': [str(out/(r['diagram']['id']+'.json')) for r in requests]}


def checked_request(req):
    payload = {k: v for k, v in req.items() if k != 'request_sha256'}
    if req.get('schema') != 'motion-render-request-v1' or digest(payload) != req.get('request_sha256'):
        raise ValueError('図解依頼のSHA不一致')
    return req


def render_remotion(request_path, out):
    req = checked_request(read(request_path))
    if req['backend'] != 'remotion':
        raise ValueError('Remotion用の依頼ではない')
    if doctor()['state'] != 'OK':
        raise ValueError('固定版Remotion実行環境が未準備。doctorを確認する')
    out.mkdir(parents=True, exist_ok=False)
    for name in ('entry.jsx', 'render.cjs'):
        shutil.copy2(HERE/'motion_backends'/name, out/name)
    atomic_json(out/'request.json', req)
    env = dict(os.environ, MOTION_REMOTION_RUNTIME=str(RUNTIME))
    done = subprocess.run([str(NODE), str(out/'render.cjs'), str(out/'request.json'),
                           str(out/'clip.mp4')], env=env, text=True, capture_output=True, timeout=900)
    (out/'render.log').write_text(done.stdout+'\n'+done.stderr)
    if done.returncode:
        raise ValueError('Remotion描画失敗: '+str(out/'render.log'))
    return {'state': 'OK', 'clip': str(out/'clip.mp4'), 'provenance': str(out/'provenance.json')}


def probe_clip(clip, req):
    done = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_streams',
                           '-of', 'json', str(clip)], capture_output=True, text=True, check=True, timeout=300)
    streams = json.loads(done.stdout)['streams']
    if len(streams) != 1 or streams[0].get('codec_type') != 'video':
        raise ValueError('図解クリップは映像1本・無音が必要')
    s = streams[0]
    if (s['width'], s['height']) != (req['width'], req['height']):
        raise ValueError('図解クリップの画面寸法が素材領域と不一致')
    if any(Fraction(s[k]) != req['fps'] for k in ('avg_frame_rate', 'r_frame_rate')):
        raise ValueError('図解クリップのfps不一致')
    if int(s['nb_read_frames']) != req['frames']:
        raise ValueError('図解クリップのフレーム数が音声時刻と不一致')
    return s


def import_clip(version, profile, request_path, clip, provenance_path):
    with (version/'inputs/.motion-assets.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _import_clip(version, profile, request_path, clip, provenance_path)


def _import_clip(version, profile, request_path, clip, provenance_path):
    """依頼・音声・コード原本・全フレームを保存。既存の受領は上書きしない。"""
    req = checked_request(read(request_path)); provenance = read(provenance_path)
    expected = {r['diagram']['id']: r for r in version_requests(version, profile)}
    did = req['diagram']['id']
    if expected.get(did) != req:
        raise ValueError('台本・音声・時刻・profileが図解依頼から変更されている')
    if (provenance.get('request_sha256') != req['request_sha256'] or
            provenance.get('backend') != req['backend'] or provenance.get('clip_sha256') != sha256(clip)
            or provenance.get('supersample') != req['supersample']):
        raise ValueError('図解クリップの出力証跡が不一致')
    if req['backend'] == 'remotion' and provenance.get('engine') != 'Remotion':
        raise ValueError('Remotionの書き出し証跡がない')
    sources = provenance.get('sources', {})
    required = {'entry.jsx', 'render.cjs'} if req['backend'] == 'remotion' else {'source.pptx', 'export.ps1'}
    if set(sources) != required:
        raise ValueError('図解の作成原本がない')
    for name, checksum in sources.items():
        if sha256(inside(provenance_path.parent, name)) != checksum:
            raise ValueError('図解の作成原本SHAが不一致')
    if req['backend'] == 'powerpoint':
        from motion_powerpoint import inspect_morph
        inspect_morph(provenance_path.parent/'source.pptx', req)
        if provenance.get('engine') != 'Microsoft.PowerPoint':
            raise ValueError('PowerPoint本体による書き出し証跡がない')
    inputs = version/'inputs'; index_path = inputs/'motion-assets.json'
    if any((version/'render'/n).exists() for n in ('render.mp4', 'video.mp4', 'render_receipt.json')):
        raise ValueError('描画済みの版へ素材を追加できない')
    index = read(index_path) if index_path.exists() else {'schema': 'motion-assets-v1', 'assets': {}}
    if did in index['assets']:
        raise ValueError('既存の図解受領を上書きできない')
    probe_clip(clip, req)
    required_space = req['width']*req['height']*3*req['frames'] + clip.stat().st_size + 1024**3
    if shutil.disk_usage(inputs).free < required_space:
        raise ValueError('図解の全フレームを安全に展開する空き容量が不足')
    dest = inputs/'motion-assets'/did; dest.mkdir(parents=True, exist_ok=False)
    shutil.copy2(clip, dest/'source.mp4')
    shutil.copy2(provenance_path, dest/'provenance.json')
    for name in sources:
        shutil.copy2(provenance_path.parent/name, dest/name)
    atomic_json(dest/'request.json', req)
    frames = dest/'frames'; frames.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-n', '-i', str(dest/'source.mp4'),
                    '-map', '0:v:0', '-fps_mode', 'passthrough', '-start_number', '0',
                    '-threads', '1', str(frames/'%06d.png')], check=True, capture_output=True, timeout=300)
    paths = sorted(frames.glob('*.png'))
    if len(paths) != req['frames']:
        raise ValueError('図解の全フレーム展開に失敗')
    hashes = {str(p.relative_to(dest)): sha256(p) for p in dest.rglob('*') if p.is_file()}
    receipt = {'schema': 'motion-asset-receipt-v1', 'request_sha256': req['request_sha256'],
               'backend': req['backend'], 'frames': req['frames'], 'fps': req['fps'],
               'size': [req['width'], req['height']], 'files': hashes,
               'semantic_review': 'NOT_CHECKED', 'pixel_review': 'NOT_CHECKED'}
    atomic_json(dest/'receipt.json', receipt)
    index['assets'][did] = {'receipt': str((dest/'receipt.json').relative_to(inputs))}
    atomic_json(index_path, index)
    return {'state': 'OK', 'diagram': did, 'frames': len(paths), 'receipt': str(dest/'receipt.json')}


def bind_assets(timeline, inputs, profile):
    """G1と直接描画の両方で全フレームの受領を照合する。"""
    expected = requests_for(inputs, timeline, profile)
    index_path = inputs/'motion-assets.json'
    index = read(index_path) if index_path.exists() else {'schema': 'motion-assets-v1', 'assets': {}}
    if index.get('schema') != 'motion-assets-v1' or set(index.get('assets', {})) != {r['diagram']['id'] for r in expected}:
        raise ValueError('必要な外部図解の受領が欠落・余剰')
    result = copy.deepcopy(timeline); dependencies = [index_path] if index_path.exists() else []
    for req in expected:
        did = req['diagram']['id']; receipt_path = inside(inputs, index['assets'][did]['receipt'])
        receipt = read(receipt_path); root = receipt_path.parent
        if (receipt.get('schema') != 'motion-asset-receipt-v1' or
                receipt.get('request_sha256') != req['request_sha256'] or
                receipt.get('backend') != req['backend'] or receipt.get('frames') != req['frames'] or
                receipt.get('fps') != req['fps'] or receipt.get('size') != [req['width'], req['height']]):
            raise ValueError('図解受領が実音声・計画と不一致')
        required = {f'frames/{i:06d}.png' for i in range(req['frames'])}
        files = receipt.get('files', {})
        if not required.issubset(files) or not {'source.mp4', 'provenance.json', 'request.json'}.issubset(files):
            raise ValueError('図解受領のフレーム・原本が欠落')
        dependencies.append(receipt_path)
        from PIL import Image
        for name, checksum in files.items():
            p = inside(root, name)
            if sha256(p) != checksum:
                raise ValueError('図解素材SHA不一致: '+name)
            if name in required:
                with Image.open(p) as im:
                    if im.size != tuple(receipt['size']):
                        raise ValueError('図解フレームの寸法不一致')
                    im.verify()
            dependencies.append(p)
        if read(root/'request.json') != req:
            raise ValueError('図解依頼原本が不一致')
        provenance = read(root/'provenance.json')
        required_sources = {'entry.jsx','render.cjs'} if req['backend']=='remotion' else {'source.pptx','export.ps1'}
        if (provenance.get('backend') != req['backend'] or
                provenance.get('request_sha256') != req['request_sha256'] or
                provenance.get('supersample') != req['supersample'] or
                provenance.get('clip_sha256') != files['source.mp4'] or
                set(provenance.get('sources',{})) != required_sources or
                any(files.get(n) != provenance['sources'][n] for n in required_sources)):
            raise ValueError('図解の原本・書き出し証跡が欠落または不一致')
        if provenance.get('engine') != ('Remotion' if req['backend']=='remotion' else 'Microsoft.PowerPoint'):
            raise ValueError('図解の書き出しエンジンが不一致')
        if req['backend']=='powerpoint':
            from motion_powerpoint import inspect_morph
            inspect_morph(root/'source.pptx',req)
        d = next(d for d in result['diagrams'] if d['id'] == did)
        d['_asset'] = {'root': str(root), 'fps': req['fps'], 'frames': req['frames']}
    result['_asset_dependencies'] = [str(p) for p in dependencies]
    return result


def render_frame(diagram, t, size):
    from PIL import Image
    a = diagram['_asset']
    idx = min(a['frames']-1, max(0, math.floor((t-diagram['start'])*a['fps']+1e-7)))
    with Image.open(Path(a['root'])/'frames'/f'{idx:06d}.png') as im:
        if im.size != size:
            raise ValueError('図解領域を描画後に拡大縮小しない')
        return im.convert('RGB')


def doctor():
    versions = {}
    for name in ('remotion', '@remotion/cli', '@remotion/renderer', '@remotion/bundler'):
        p = RUNTIME/'node_modules'/name/'package.json'
        versions[name] = read(p)['version'] if p.is_file() else None
    paths = {'node': str(NODE) if NODE.is_file() else None,
             'chrome': shutil.which('google-chrome'), 'ffmpeg': shutil.which('ffmpeg'),
             'ffprobe': shutil.which('ffprobe'),
             'font': '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'}
    ready = all(paths.values()) and Path(paths['font']).is_file() and set(versions.values()) == {'4.0.535'}
    return {'state': 'OK' if ready else 'FAIL', 'platform': platform.platform(),
            'remotion': {'ready': bool(ready), 'versions': versions, 'runtime': str(RUNTIME)},
            'powerpoint': {'authoring': 'PPTX_MORPH_XML_SUPPORTED',
                           'native_export': 'NOT_AVAILABLE_ON_LINUX',
                           'required': 'Windows + licensed Microsoft PowerPoint + FFmpeg/FFprobe + Noto Sans CJK JP',
                           'plugin_search': '2026-10-17: PowerPointモーフ書き出しを実行できる接続は未確認'},
            'paths': paths, 'canonical_pipeline': str(HERE),
            'scope': '設定とファイルの検査。実描画の合格は検証ログで確認する'}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__); sub = ap.add_subparsers(dest='action', required=True)
    sub.add_parser('doctor')
    p = sub.add_parser('prepare'); p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('import'); p.add_argument('--request', type=Path, required=True)
    p.add_argument('--clip', type=Path, required=True); p.add_argument('--provenance', type=Path, required=True)
    for name in ('prepare', 'import'):
        sub.choices[name].add_argument('--version', type=Path, required=True)
        sub.choices[name].add_argument('--profile', type=Path, required=True)
    for name in ('remotion', 'powerpoint'):
        p = sub.add_parser(name); p.add_argument('--request', type=Path, required=True)
        p.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.action == 'doctor': result = doctor()
        elif a.action == 'prepare': result = prepare(a.version, read(a.profile), a.out)
        elif a.action == 'import': result = import_clip(a.version, read(a.profile), a.request, a.clip, a.provenance)
        elif a.action == 'remotion': result = render_remotion(a.request, a.out)
        else:
            from motion_powerpoint import prepare_powerpoint
            result = prepare_powerpoint(a.request, a.out)
        print(json.dumps(result, ensure_ascii=False)); return 1 if result.get('state') == 'FAIL' else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        print(json.dumps({'state': 'FAIL', 'error': str(e)}, ensure_ascii=False)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
