"""Task-local material preparation with immutable plans and resumable outputs."""
import argparse
import asyncio
import fcntl
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlparse

from artifact_receipts import file_hashes
from pipeline_common import atomic_json, sha256, utcnow
from production_policy import batch_mode
from story_contract import norm, original_quote

FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')


def inside(task, name):
    path = (Path(task)/name).resolve()
    if not path.is_relative_to(Path(task).resolve()):
        raise ValueError('素材パスがtaskの範囲外: ' + name)
    return path


def plan_inputs(task):
    task = Path(task)
    paths = [task/n for n in ('story-pack.json', 'material-sheet-v1.json', 'capture/targets.json')]
    pack = json.loads(paths[0].read_text())
    paths += [inside(task, 'sources/'+s['source_id']+'.txt') for s in pack['sources']]
    return file_hashes(paths)


def source_for(task, pack, item):
    source = next((s for s in pack['sources'] if s['url'] == item['source_url']), None)
    if source is None:
        raise ValueError(item['id'] + ': 出典URLがpackにない')
    text = inside(task, 'sources/'+source['source_id']+'.txt').read_text()
    if sha256(inside(task, 'sources/'+source['source_id']+'.txt')) != source['text_sha256']:
        raise ValueError(item['id'] + ': 取得原文SHA不一致')
    return source, text


def quoted_text(task, pack, item, locator):
    source, text = source_for(task, pack, item)
    linked = {fid for line in pack['lines'] if line['scene_id'] == item['scene_id']
              for fid in line.get('fact_ids', [])}
    candidates = [f['quote'] for f in pack['facts'] if f['fact_id'] in linked
                  and f['source_id'] == source['source_id'] and len(f['quote']) <= 650]
    # Copy the actual substring from the saved source, never retype/paraphrase a quote.
    for value in candidates + [locator]:
        quote = original_quote(value, text)
        if quote:
            return source, quote
    raise ValueError(item['id'] + ': 引用元の原文が見つからない')


def wrap(draw, text, font, width):
    lines = []
    for paragraph in text.splitlines() or ['']:
        line = ''
        tokens = re.findall(r'[A-Za-z0-9][A-Za-z0-9’\x27.,:/?=_-]*\s*|.', paragraph)
        for token in tokens:
            if draw.textlength(line+token, font=font) > width and line:
                lines.append(line.rstrip()); line = ''
            # Long URLs and unspaced Japanese still have a bounded layout.
            for char in token:
                if draw.textlength(line+char, font=font) > width and line:
                    lines.append(line.rstrip()); line = ''
                line += char
        lines.append(line.rstrip())
    return '\n'.join(lines)


def draw_quote(item, source, quote, output, heading):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new('RGB', (1280, 660), '#0c1524'); d = ImageDraw.Draw(im)
    font = lambda size: ImageFont.truetype(str(FONT), size)
    d.rectangle((0, 0, 18, 660), fill='#36d6b4')
    d.text((48, 25), '公式資料の原文参照図（独自作成）', font=font(20), fill='#61d9c2')
    title = wrap(d, heading, font(30), 1160)
    d.multiline_text((48, 66), title, font=font(30), fill='white', spacing=5)
    d.rounded_rectangle((48, 162, 292, 454), radius=16, fill='#17283b', outline='#61d9c2', width=2)
    d.text((68, 184), '出典', font=font(24), fill='white')
    d.multiline_text((68, 234), wrap(d, urlparse(source['url']).hostname or '公式資料', font(20), 204),
                     font=font(20), fill='#d9e5ee', spacing=10)
    d.text((68, 386), item['id']+' / 原文との対応', font=font(15), fill='#61d9c2')
    d.line((292, 300, 332, 300), fill='#61d9c2', width=3)
    d.rounded_rectangle((332, 162, 1232, 454), radius=16, fill='#f5f7fa')
    d.text((356, 180), 'SOURCE QUOTE / 原文抜粋', font=font(16), fill='#536273')
    size = 22
    while size > 16:
        value = wrap(d, quote, font(size), 850)
        if d.multiline_textbbox((0, 0), value, font=font(size), spacing=7)[3] <= 213:
            break
        size -= 1
    if d.multiline_textbbox((0, 0), value, font=font(size), spacing=7)[3] > 213:
        raise ValueError(item['id'] + ': 引用が表示領域に収まらない')
    d.multiline_text((356, 220), value, font=font(size), fill='#142236', spacing=7)
    d.text((48, 474), '表示箇所：'+item.get('translation', item['id']), font=font(21), fill='white')
    d.text((48, 518), '原文と出典の対応を示す図です。ウェブページの画面ではありません。', font=font(16), fill='#c7d2e0')
    url = wrap(d, source['url'], font(14), 1160)
    d.multiline_text((48, 552), url, font=font(14), fill='#8fe7d4', spacing=4)
    d.text((48, 626), '内部確認用／公開前の権利確認は別途', font=font(14), fill='#98a8bb')
    im.save(output, optimize=True)


def current_receipt(task):
    task = Path(task)
    p = task/'capture/materials-receipt.json'
    if not p.is_file():
        return None
    receipt = json.loads(p.read_text())
    if receipt.get('schema') != 'prepared-materials-v1':
        raise ValueError('素材受領の形式が不正')
    if receipt.get('input_hashes') != plan_inputs(task):
        raise ValueError('素材準備後に入力・実装が変わった。素材準備を再開する')
    if not receipt.get('items'):
        raise ValueError('素材受領が空')
    for row in receipt['items']:
        if row.get('capture_manifest') and sha256(inside(task,row['capture_manifest'])) != row.get('capture_manifest_sha256'):
            raise ValueError(row['id']+': 撮影manifestのSHA不一致')
        if sha256(inside(task, row['file'])) != row['sha256']:
            raise ValueError(row['id'] + ': 素材SHA不一致')
    return receipt


def resolved_materials(task):
    task = Path(task)
    items = json.loads((task/'material-sheet-v1.json').read_text())['items']
    if not (task/'capture/materials-receipt.json').is_file():
        return items
    receipt = current_receipt(task)
    rows = {r['id']: r for r in receipt['items']}
    if set(rows) != {i['id'] for i in items} or len(rows) != len(receipt['items']):
        raise ValueError('素材受領と素材計画のIDが不一致')
    for item in items:
        row = rows[item['id']]
        if row['source_url'] != item['source_url'] or row['scene_id'] != item['scene_id']:
            raise ValueError(item['id'] + ': 素材の出典・場面が不一致')
    return [{**item, **rows[item['id']]} for item in items]


def verify(task):
    from PIL import Image
    task = Path(task)
    pack = json.loads((task/'story-pack.json').read_text())
    items = resolved_materials(task)
    targets = {t['id']: t for t in json.loads((task/'capture/targets.json').read_text())}
    manifests={}
    if not (task/'capture/materials-receipt.json').is_file():
        for folder in ('screenshots','screenshots_v2','screenshots_v3','screenshots_v4','screenshots_v5','assembled'):
            path=task/'capture'/folder/'manifest.json'
            if path.is_file():
                for entry in json.loads(path.read_text()):manifests[entry['id']]=entry
    warnings, seen, covered = [], {}, set()
    for item in items:
        if not re.fullmatch(r'[A-Za-z0-9_]+', item['id']):
            raise ValueError('素材IDが不正')
        source, text = source_for(task, pack, item)
        if item.get('use') != 'internal' or 'internal_only' not in item.get('rights', ''):
            raise ValueError(item['id'] + ': 内部確認用の素材条件と不一致')
        path = inside(task, item['file'])
        with Image.open(path) as im:
            im.load()
            if not im.width or not im.height:
                raise ValueError(item['id'] + ': 空の画像')
            if im.height > item.get('max_height_px', 700):
                raise ValueError(item['id'] + ': 画像高さが表示領域を超える')
        if item.get('kind') == 'explain_diagram':
            if not item.get('quote_text') or not original_quote(item['quote_text'], text):
                raise ValueError(item['id'] + ': 原文にない引用図')
        else:
            target = targets.get(item['page_key'])
            if not target or target['url'] != source['url']:
                raise ValueError(item['id'] + ': 撮影対象の出典が不一致')
        digest = sha256(path)
        if item.get('kind')=='screenshot' and manifests:
            entry=manifests.get(item['page_key'],{})
            crop=next((c for c in entry.get('crops',[]) if c.get('name')==item['crop']),{})
            if entry.get('error') or not crop.get('found') or crop.get('sha256')!=digest or entry.get('url')!=item['source_url']:
                raise ValueError(item['id']+': 撮影manifestと受領画像が不一致')
        elif item.get('kind')=='screenshot' and not (task/'capture/materials-receipt.json').is_file():
            raise ValueError(item['id']+': 撮影manifestがない')
        if digest in seen:
            warnings.append(item['id'] + ': '+seen[digest]+' と同じ原文段落を表示')
        seen[digest] = item['id']; covered.add(item['scene_id'])
    if {s['scene_id'] for s in pack['scenes']} != covered:
        raise ValueError('素材のない場面がある')
    if warnings and not batch_mode():
        raise ValueError('; '.join(warnings))
    return {'state': 'OK', 'items': len(items), 'scenes': len(covered), 'warnings': warnings}


def prepare(task, capture=None):
    task = Path(task).resolve(); (task/'capture').mkdir(exist_ok=True)
    with (task/'capture/.prepare-materials.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            receipt = current_receipt(task)
            if receipt is not None:
                return {**verify(task), 'reused': True, 'receipt': str(task/'capture/materials-receipt.json')}
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Keep old attempt files; a fresh attempt gets its own directory.
        inputs = plan_inputs(task)
        pack = json.loads((task/'story-pack.json').read_text())
        items = json.loads((task/'material-sheet-v1.json').read_text())['items']
        targets = json.loads((task/'capture/targets.json').read_text())
        attempt = task/'capture/attempts'/uuid.uuid4().hex; attempt.mkdir(parents=True)
        if capture is None:
            from material_capture import capture_targets
            capture = lambda ts, out: asyncio.run(capture_targets(ts, out))
        for item in items:
            source_for(task, pack, item)
        manifest = capture(targets, attempt)
        atomic_json(attempt/'manifest.json',manifest)
        pages = {p['id']: p for p in manifest}; output, warnings = [], []
        target_map = {t['id']: t for t in targets}
        headings = {s['scene_id']: s['heading'] for s in pack['scenes']}
        for item in items:
            source_for(task, pack, item)
            page = pages.get(item['page_key'], {})
            crop = next((c for c in page.get('crops', []) if c['name'] == item['crop']), {})
            row = {k: item[k] for k in ('id', 'scene_id', 'source_url')}
            if not page.get('error') and crop.get('found') and page.get('url') == item['source_url']:
                image = (attempt/crop['file']).resolve()
                if not image.is_relative_to(attempt) or sha256(image) != crop['sha256']:
                    raise ValueError(item['id'] + ': 撮影元のSHA・保存先が不一致')
                row.update(kind='screenshot', file=str(image.relative_to(task)),
                           sha256=sha256(image), captured_utc=page['captured_utc'])
            else:
                if not batch_mode():
                    raise ValueError(item['id'] + ': 撮影失敗: ' + str(page.get('error', crop.get('error'))))
                locator = next(c['text'] for c in target_map[item['page_key']]['crops'] if c['name'] == item['crop'])
                source, quote = quoted_text(task, pack, item, locator)
                image = attempt/(item['id']+'_quote.png')
                draw_quote(item, source, quote, image, headings[item['scene_id']])
                row.update(kind='explain_diagram', file=str(image.relative_to(task)),
                           sha256=sha256(image), quote_text=quote, generated_by_ai=False,
                           fallback_reason=page.get('error', crop.get('error', 'capture missing')))
                warnings.append(item['id'] + ': 撮影不可のため原文参照図を使用')
            row.update(capture_manifest=str((attempt/'manifest.json').relative_to(task)),capture_manifest_sha256=sha256(attempt/'manifest.json'))
            output.append(row)
        if plan_inputs(task) != inputs:
            raise ValueError('素材準備中に入力が変わった')
        receipt = {'schema': 'prepared-materials-v1', 'created_utc': utcnow(),
                   'input_hashes': inputs, 'items': output, 'warnings': warnings,
                   'unique_urls': len({t['url'] for t in targets}), 'attempt': str(attempt)}
        atomic_json(attempt/'materials-receipt.json', receipt)
        atomic_json(task/'capture/materials-receipt.json', receipt)
        checked=verify(task)
        return {**checked, 'reused': False, 'warnings': warnings + checked['warnings'],
                'receipt': str(task/'capture/materials-receipt.json')}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--task', required=True, type=Path)
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    print(json.dumps(verify(a.task) if a.verify else prepare(a.task), ensure_ascii=False))


if __name__ == '__main__':
    main()
