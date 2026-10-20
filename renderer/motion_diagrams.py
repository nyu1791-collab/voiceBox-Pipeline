"""根拠付き図解の単一定義。台詞ID→実音声時刻→同じ描画関数→G1/preview。"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import math
import re
from functools import lru_cache
from urllib.parse import urlparse

SCHEMA = 'motion-plan-v1'
TEMPLATES = ('rows', 'bars', 'steps')

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def automatic_plan(pack):
    """本文を切り詰めず再利用。因果・数値・要約は自動で創作しない。"""
    diagrams = []
    for scene in pack['scenes']:
        selected = []
        for line in pack['lines']:
            if (line['scene_id'] == scene['scene_id'] and line.get('claim_type') == 'reported'
                    and line.get('fact_ids') and 0 < len(line['text']) <= 100
                    and line['text'] not in [x['label'] for x in selected]):
                selected.append({'id': line['line_id'], 'line_id': line['line_id'],
                                 'label': line['text'], 'fact_ids': list(line['fact_ids'])})
            if len(selected) == 3:
                break
        if len(selected) >= 2:
            diagrams.append({'id': 'D_' + scene['scene_id'], 'scene_id': scene['scene_id'],
                             'title': scene['heading'], 'template': 'rows', 'elements': selected})
    return {'schema': SCHEMA, 'strategy': 'dialogue_rows', 'diagrams': diagrams,
            'omitted_scenes': [s['scene_id'] for s in pack['scenes'] if not any(d['scene_id'] == s['scene_id'] for d in diagrams)]}

def plan_errors(plan, pack):
    errors = []
    def need(ok, message):
        if not ok: errors.append('図解: ' + message)
    if not isinstance(plan, dict): return ['図解: 計画はobjectが必要']
    need(plan.get('schema') == SCHEMA, 'schema不正')
    diagrams = plan.get('diagrams')
    if not isinstance(diagrams, list): return errors + ['図解: diagramsはlistが必要']
    lines = {l['line_id']: l for l in pack['lines']}
    facts = {f['fact_id']: f for f in pack['facts']}
    scenes = {s['scene_id']: s for s in pack['scenes']}
    sources = {s['source_id']: s for s in pack['sources']}
    used = set(); ids = set()
    for d in diagrams:
        if not isinstance(d, dict): errors.append('図解: 項目はobjectが必要'); continue
        name = d.get('id'); sid = d.get('scene_id'); template = d.get('template')
        need(isinstance(name, str) and bool(re.fullmatch(r'[A-Za-z0-9_]+', name)) and name not in ids, 'ID欠落・不正・重複')
        ids.add(str(name))
        need(sid in scenes and sid not in used, '場面が不明または同一場面に複数図解')
        used.add(sid)
        need(template in TEMPLATES, '未対応のtemplate')
        need(d.get('backend', 'pillow') in ('pillow', 'remotion', 'powerpoint'), '未対応のbackend')
        need(isinstance(d.get('title'), str) and 0 < len(d['title']) <= 30, '題名は1〜30文字')
        elements = d.get('elements')
        if not isinstance(elements, list) or not 2 <= len(elements) <= 3:
            errors.append('図解: 部品数は2〜3'); continue
        order = []; eids = set(); units = set()
        for e in elements:
            if not isinstance(e, dict): errors.append('図解: 部品はobjectが必要'); continue
            lid = e.get('line_id'); line = lines.get(lid, {}); label = e.get('label'); refs = e.get('fact_ids')
            need(isinstance(e.get('id'), str) and bool(e['id']) and e['id'] not in eids, '部品ID欠落・重複')
            eids.add(str(e.get('id')))
            need(line.get('scene_id') == sid and line.get('claim_type') == 'reported', '部品は同じ場面の説明台詞へ結ぶ')
            need(isinstance(label, str) and 0 < len(label) <= 100 and '\n' not in label, 'ラベルは改行なし1〜100文字')
            need(isinstance(refs, list) and bool(refs) and len(set(refs)) == len(refs)
                 and all(f in facts and f in line.get('fact_ids', []) for f in refs), '部品の根拠が台詞と不一致')
            if isinstance(refs, list):
                need(all(facts.get(f, {}).get('source_id') in sources for f in refs), '出典が不明')
            # 断定の追加や省略による意味変更を避ける。ラベルは台詞の連続部分だけ。
            need(isinstance(label, str) and label in line.get('text', ''), 'ラベルが対応台詞にない')
            order.append(list(lines).index(lid) if lid in lines else -1)
            if template == 'bars':
                v = e.get('value'); quote = e.get('value_quote'); fid = e.get('value_fact_id'); unit = e.get('unit')
                need(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1e12, '棒の値は有限の非負数')
                need(isinstance(unit, str) and bool(unit) and len(unit) <= 12, '棒には単位が必要')
                units.add(str(unit))
                raw = facts.get(fid, {}).get('quote', '')
                need(fid in (refs or []) and isinstance(quote, str) and bool(quote) and quote in raw, '値の根拠引用が原文にない')
                numbers = re.findall(r'(?<![\d.])\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])', str(quote))
                need(type(v) in (int, float) and any(float(n.replace(',', '')) == v for n in numbers), '棒の値と引用の数値が不一致')
                need(isinstance(unit, str) and unit in str(quote), '単位が値の引用にない')
        need(all(a < b for a, b in zip(order, order[1:])), '部品の台詞順序が逆または重複')
        if template == 'bars':
            metric = d.get('metric')
            need(isinstance(metric,str) and bool(metric), '棒グラフは共通の指標名metricが必要')
            need(all(isinstance(metric,str) and metric in str(e.get('value_quote','')) for e in elements), '指標が値の引用にない')
            need(len(units) == 1, '異なる単位を同じ軸で比較しない')
            need(any(type(e.get('value')) in (int, float) and e['value'] > 0 for e in elements), '棒の最大値は正数が必要')
        if template == 'steps':
            need(d.get('relation') == 'sequence', 'stepsは手順順序を明示する（因果を自動推測しない）')
    omitted = plan.get('omitted_scenes', [])
    need(isinstance(omitted, list) and len(set(omitted)) == len(omitted)
         and set(omitted) == set(scenes) - used, '描かない場面もomitted_scenesに明示')
    return errors

def compile_timeline(plan, pack, records):
    errors = plan_errors(plan, pack)
    rows = {r['id']: r for r in records}
    if len(rows) != len(records): errors.append('図解: 音声台詞ID重複')
    for line in pack['lines']:
        row = rows.get(line['line_id'], {})
        if row.get('voice_text') != line['text'] or row.get('scene_id') != line['scene_id']:
            errors.append('図解: 音声と台本が不一致 ' + line['line_id'])
    if errors: raise ValueError('\n'.join(errors))
    compiled = []
    for diagram in plan['diagrams']:
        d = copy.deepcopy(diagram)
        # 一次資料の画面を図解で隠し続けない。各素材の初出で先に実画像を見せる。
        seen_visuals = set(); windows = []
        line_map = {l['line_id']: l for l in pack['lines']}
        for row in records:
            if row.get('scene_id') != d['scene_id'] or line_map.get(row['id'], {}).get('claim_type') == 'chapter': continue
            visual = row.get('visual_id')
            if visual and visual not in seen_visuals:
                seen_visuals.add(visual)
                windows.append([row['start'], row['start'] + min(1.2,(row['end']-row['start'])*0.3)])
        d['evidence_windows'] = windows
        for e in d['elements']:
            row = rows[e['line_id']]
            start, end = row.get('start'), row.get('end')
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
                    or end-start < 0.6):
                raise ValueError('図解: 部品の音声時刻が不正または0.6秒未満')
            start = max([start] + [b for a,b in windows if a <= start < b])
            e['start'] = start
            e['duration'] = min(0.7, (end-start)*0.45)
            e['end'] = end
        d['start'] = d['elements'][0]['start']
        d['end'] = d['elements'][-1]['end']
        for a, b in zip(d['elements'], d['elements'][1:]):
            if a['end'] > b['start'] + 1e-6: raise ValueError('図解: 台詞が重複')
        d['source_ids'] = list(dict.fromkeys(next(f['source_id'] for f in pack['facts'] if f['fact_id'] == fid) for e in d['elements'] for fid in e['fact_ids']))
        d['source_hosts'] = list(dict.fromkeys(urlparse(s.get('url', '')).hostname or s.get('title',s['source_id']) for s in pack['sources'] if s['source_id'] in d['source_ids']))
        d['source_labels'] = list(dict.fromkeys(
            next(s.get('title', s['source_id']) for s in pack['sources'] if s['source_id'] ==
                 next(f['source_id'] for f in pack['facts'] if f['fact_id'] == fid))
            for e in d['elements'] for fid in e['fact_ids']))
        compiled.append(d)
    return {'schema': 'motion-timeline-v1', 'plan_sha256': digest(plan), 'diagrams': compiled}

def attach_to_version(pack, task, inputs, presentation, records):
    if 'motion_plan' not in pack: return
    plan = pack['motion_plan']
    if json.loads((task/'motion-plan.json').read_text()) != plan:
        raise ValueError('図解: packと出力計画が不一致')
    timeline = compile_timeline(plan, pack, records)
    for filename, value in [('motion-plan.json', plan), ('motion-source.json', pack)]:
        (inputs/filename).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    timeline['source_sha256'] = digest(pack)
    presentation['motion_diagrams'] = timeline


def active_diagram(timeline, t):
    return next((d for d in timeline.get('diagrams', []) if d['start'] <= t < d['end'] and not any(a <= t < b for a,b in d.get('evidence_windows',[]))), None)

def cache_state(timeline, t, fps):
    d = active_diagram(timeline, t)
    if d and '_asset' in d:
        return (d['id'], min(d['_asset']['frames']-1, max(0, math.floor((t-d['start'])*fps+1e-7))))
    return (d['id'], tuple(min(round((t-e['start'])*fps), math.ceil(e['duration']*fps))
                            if t >= e['start'] else -1 for e in d['elements'])) if d else None

@lru_cache(maxsize=128)
def _font(font, size):
    from PIL import ImageFont
    return ImageFont.truetype(str(font), size)

@lru_cache(maxsize=256)
def _fit(label, font_path, width, height, max_size=25):
    for size in range(max_size, 17, -1):
        font = _font(str(font_path), size); lines = []; line = ''
        for ch in label:
            if line and font.getlength(line+ch) > width:
                lines.append(line); line = ''
            line += ch
        if line: lines.append(line)
        if all(font.getlength(s) <= width for s in lines) and len(lines)*(size+5) <= height:
            return font, lines
    raise ValueError('図解: 最小18pxでも文字が枠に収まらない。台詞/部品計画を見直す')

def render_panel(diagram, t, size, font_path, supersample=4):
    """開始・途中・完了・本番に共通。音声の絶対時刻を受け取る。"""
    if diagram.get('backend', 'pillow') != 'pillow':
        if '_asset' not in diagram: raise ValueError('外部図解の受領検査が未完了')
        from motion_assets import render_frame
        return render_frame(diagram, t, size)
    from PIL import Image, ImageDraw
    width, height = size
    if width < 400 or height < 250: raise ValueError('図解: 素材領域が小さすぎる')
    ss = supersample
    if type(ss) is not int or not 1 <= ss <= 4: raise ValueError('図解: supersampleは1〜4')
    im = Image.new('RGB', (width*ss, height*ss), '#FAF8F3'); draw = ImageDraw.Draw(im)
    def box(xy, fill, outline=None):
        draw.rounded_rectangle(tuple(round(v*ss) for v in xy), radius=9*ss, fill=fill, outline=outline, width=2*ss)
    def text(x, y, label, f, color='#1E2430'):
        draw.text((round(x*ss), round(y*ss)), label, font=_font(str(font_path), f.size*ss), fill=color)
    title_font, title = _fit(diagram['title'], font_path, width-32, 36, 25)
    for line in title: text(16, 8, line, title_font)
    elements = diagram['elements']; n = len(elements); row_h = (height-83)/n
    if row_h < 60: raise ValueError('図解: 部品の縦幅が不足')
    template = diagram['template']
    max_value = max(e.get('value', 0) for e in elements) if template == 'bars' else 1
    active = max((i for i,e in enumerate(elements) if t >= e['start']), default=-1)
    for i, e in enumerate(elements):
        y = 49+i*row_h; h = row_h-7
        if template == 'bars':
            font, lines = _fit(e['label'], font_path, width-36, h-28, 21)
        else: font, lines = _fit(e['label'], font_path, width-70, h-10, 24)
        if t < e['start']: continue
        progress = min(1, max(0, (t-e['start'])/e['duration'])); eased = 1-(1-progress)**3
        offset = 14*(1-eased)
        box((12+offset, y, width-12, y+h), '#FFFFFF', '#E88014' if i == active else '#D7DFD8')
        if template == 'bars':
            for j, label in enumerate(lines): text(20+offset, y+3+j*(font.size+5), label, font)
            bw = (width-170)*e['value']/max_value*eased
            if bw > 0: box((20, y+h-23, 20+bw, y+h-7), '#269668')
            val = f"{e['value']:g}{e['unit']}"; vf = _font(str(font_path), 18)
            text(width-140, y+h-27, val, vf)
        else:
            number = str(i+1) if template == 'steps' else '●'
            text(22+offset, y+max(0,(h-26)/2), number, _font(str(font_path), 22), '#269668')
            for j,label in enumerate(lines): text(54+offset, y+5+j*(font.size+5), label, font)
            if template == 'steps' and i > 0:
                # 縦の流れ。次の部品が現れた時だけ接続を出す。
                x=35*ss; top=round((y-8)*ss); bottom=round(y*ss)
                draw.line((x,top,x,bottom),fill='#269668',width=2*ss)
                draw.polygon([(x-4*ss,bottom-5*ss),(x+4*ss,bottom-5*ss),(x,bottom)],fill='#269668')
    footer = '説明図／出典：' + '・'.join(diagram['source_hosts'])
    ff, fl = _fit(footer, font_path, width-28, 31, 18)
    for j,label in enumerate(fl): text(14, height-36+j*(ff.size+5), label, ff)
    return im.resize((width,height), Image.Resampling.LANCZOS) if ss != 1 else im

def bound_timeline(presentation, records, inputs, require_assets=True, profile=None):
    """描画開始前にも照合する。G1を迂回した直接描画でも改変を許可しない。"""
    timeline = presentation.get('motion_diagrams')
    if timeline is None: return None
    pack = json.loads((inputs/'motion-source.json').read_text())
    plan = json.loads((inputs/'motion-plan.json').read_text())
    if pack.get('motion_plan') != plan: raise ValueError('図解: 保存したpackとplanが不一致')
    expected = compile_timeline(plan,pack,records); expected['source_sha256'] = digest(pack)
    if timeline != expected: raise ValueError('図解: コンパイル結果が計画・音声と不一致')
    if require_assets and (any(d.get('backend', 'pillow') != 'pillow' for d in timeline['diagrams'])
                           or (inputs/'motion-assets.json').exists()):
        from motion_assets import bind_assets
        if profile is None: raise ValueError('外部図解の検査には描画profileが必要')
        return bind_assets(timeline, inputs, profile)
    return timeline

def audit_timeline(presentation, records, inputs, font_path, profile, out=None):
    """台詞・数値・時刻を照合し、開始/途中/完了を実フォントで画素検査。"""
    timeline = bound_timeline(presentation,records,inputs,profile=profile)
    if timeline is None: return {'state':'NOT_APPLICABLE','diagrams':0}
    x1,y1,x2,y2 = profile['layout']['zones']['evidence']; size = (x2-x1,y2-y1)
    caption = profile['layout']['zones']['caption']
    if max(y1,caption[1]) < min(y2,caption[3]) and max(x1,caption[0]) < min(x2,caption[2]):
        raise ValueError('図解: 素材領域と字幕領域が重なる')
    fps = profile['layout']['fps']; samples=[]
    for d in timeline['diagrams']:
        for i,e in enumerate(d['elements']):
            # 実際に存在するフレームの時刻へ丸める。
            before = max(d['start'], e['start']-1/fps)
            middle = math.ceil((e['start']+e['duration']/2)*fps)/fps
            settled = math.ceil((e['start']+e['duration'])*fps)/fps
            if not settled < e['end']: raise ValueError('図解: 完了状態を表示するフレームがない')
            images=[render_panel(d,t,size,font_path,4) for t in (before,middle,settled)]
            hashes=[hashlib.sha256(im.tobytes()).hexdigest() for im in images]
            if len(set(hashes)) != 3: raise ValueError('図解: 開始・途中・完了の画素が変化しない')
            if out:
                out.mkdir(parents=True,exist_ok=True)
                for name,im in zip(('before','middle','settled'),images): im.save(out/f"{d['id']}_{i}_{name}.png")
            samples.append({'diagram_id':d['id'],'element_id':e['id'],'times':[before,middle,settled],'pixel_sha256':hashes})
    result={'state':'OK','diagrams':len(timeline['diagrams']),'elements':len(samples),'samples':samples,
            'asset_dependencies':timeline.get('_asset_dependencies',[]),
            'scope':'計画・根拠・実時刻・描画状態の機械検査。意味の全面監査・聴感確認の代替ではない'}
    if out: (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--version',type=Path,required=True)
    ap.add_argument('--profile',type=Path,required=True)
    ap.add_argument('--font',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();inputs=a.version/'inputs'
    if a.out.exists(): ap.error('出力先は新しい場所を指定する')
    try:
        result=audit_timeline(json.loads((inputs/'presentation.json').read_text()),json.loads((inputs/'timing.json').read_text())['records'],inputs,a.font,json.loads(a.profile.read_text()),a.out)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'state':'FAIL','error':str(exc)},ensure_ascii=False));return 1

if __name__=='__main__': raise SystemExit(main())

