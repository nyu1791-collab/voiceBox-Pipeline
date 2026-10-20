#!/usr/bin/env python3
"""Render the approved landscape AI-tech briefing format from measured narration.

This renderer reuses native Zundamon/Metan layers and measured VOICEVOX audio.
It performs one H.264 encode and does not synthesize narration or generate images.
"""
from __future__ import annotations
from pathlib import Path
import os
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contextlib import closing
from media_editing import VideoPanels
from evidence_translation import TranslationCards
from subtitle_tools import wrap_japanese, phrase_breaks
from motion_diagrams import bound_timeline, active_diagram, cache_state as motion_cache_state, render_panel as render_motion_panel
from production_cues import draw_direction, cache_state as direction_cache_state
import argparse, hashlib, json, math, re, signal, wave, time
from collections import OrderedDict
from functools import lru_cache  # 字幕の折り返しの結果の再利用（フォントは共有しない）
from array import array
from pathlib import Path
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from render_reusable_short import character_variants, _whole_post_source_visual, _display_source_url
from visual_contract import PEEK_ENABLED, CHAPTER_CARD_CHARACTERS, MOTION_TEXT_IN_VISUAL_SLOT, check_summary, summary_lines, summary_box, SUMMARY_LINE_H
# FIX v1.4 (ADD-16): extra authored expressions from the approved shell parts (SURPRISED / TROUBLED / SKEPTICAL)
import render_reusable_short as _rs
_BASE_CHAR_LAYERS = _rs.character_layers
_EXTRA_EXPRESSIONS = {
    "Zundamon": {
        "SURPRISED": ["目/目セット/見開き白目.png", "目/目セット/黒目/普通目.png", "眉/上がり眉.png"],
        "TROUBLED": ["目/目セット/普通白目.png", "目/目セット/黒目/普通目.png", "眉/困り眉2.png"],
        "SKEPTICAL": ["目/目セット/ジト白目.png", "目/目セット/黒目/普通目.png", "眉/普通眉.png"],
    },
    "Metan": {
        "SURPRISED": ["目/目セット/見開き白目.png", "目/目セット/黒目/普通目.png", "眉/ごきげん.png"],
        "TROUBLED": ["目/目セット/普通白目.png", "目/目セット/黒目/普通目.png", "眉/太眉こまり.png"],
        "SKEPTICAL": ["目/目セット/普通白目.png", "目/目セット/黒目/普通目.png", "眉/ややおこ.png"],
    },
}
def _character_layers_v14(character):
    fixed, mouths, expressions = _BASE_CHAR_LAYERS(character)
    merged = dict(expressions)
    merged.update(_EXTRA_EXPRESSIONS[character])
    return fixed, mouths, merged
_rs.character_layers = _character_layers_v14
from validate_video_content_contract import validate_content_contract
from render_preflight import validate_render_plan, render_frame_count
from media_encoder_process import encoder_process, interrupt_encoder


def wrap(text, font, width):
    lines=[]; line=""
    for token in re.findall(r"[A-Za-z0-9]+(?:[._:/+-][A-Za-z0-9]+)*|.", str(text)):
        if font.getlength(line+token)>width and line and token not in "、。！？：；」』】":
            lines.append(line); line=""
        line += token
    if line: lines.append(line)
    return lines


def fit_text(text, font_path, start_size, min_size, width, max_lines):
    for size in range(start_size, min_size-1, -2):
        f=_font(str(font_path), size)
        lines=wrap(text, f, width)
        if len(lines)<=max_lines:
            return f, lines
    f=_font(str(font_path), min_size)
    _l = wrap(text, f, width)
    if len(_l) > max_lines: raise ValueError("caption exceeds max lines at minimum size; reword or split the line")  # FIX v1.4: no silent truncation
    return f, _l


import re
CLOSE_PUNCT = "、。！？：；」』】"
PARTICLES = set("はがをにでとのへもやかねよ")
def _kind(c):
    if re.match(r"[ぁ-ん]", c): return "h"
    if re.match(r"[ァ-ヶー]", c): return "k"
    if re.match(r"[一-龯々〆]", c): return "K"
    if re.match(r"[A-Za-z0-9]", c): return "a"
    return "o"
def _break_ok(prev, nxt):
    """語の途中で切らない（RULES §4、2026-10-16 改定）。切ってよいのは：句読点の後、助詞の前（直前がかな・カタカナ・英数字。
    漢字の後の助詞は、か・や・ね・よ を除く主要な助詞だけ）、ひらがな→漢字・カタカナ・英数字、カタカナ・英数字・漢字どうしの語の境目（漢字どうしは切らない）。"""
    if not prev or not nxt: return False
    if nxt in CLOSE_PUNCT: return False
    if prev in "、。！？…」』】": return True
    kp, kn = _kind(prev), _kind(nxt)
    if nxt in PARTICLES and (kp in "hka" or (kp == "K" and nxt in "はがをにでとのへも")): return True
    if kp == "h" and kn in "kKa": return True
    if kp in "kaK" and kn in "kaK" and not (kp == "K" and kn == "K"): return True
    return False
def wrap_words(text, font, width, strict=False):
    """BudouXの文節境界で折り返し、数値・単位・英字語を保持する。"""
    return wrap_japanese(text, font, width, strict=strict)


@lru_cache(maxsize=64)
def _contained_visual(src, box_w, box_h):
    # 同じ画像を同じ大きさに縮めた結果は毎回同じ。フレームごとの読み直しを避ける（出力は変えない）
    with Image.open(src) as opened:
        return ImageOps.contain(opened.convert("RGB"),(box_w,box_h),Image.Resampling.LANCZOS)

@lru_cache(maxsize=128)
def _font(path, size):
    # フォントの読み込みを共通化（同じフォントは同じ結果。出力は変えない）
    return ImageFont.truetype(path, size)

@lru_cache(maxsize=32)
def _src_rgb(src):
    # 元の画像を一度だけ読む（切り抜きの元。読み取り専用、出力は変えない）
    with Image.open(src) as o:
        return o.convert("RGB")

@lru_cache(maxsize=16)
def _resized_bg(src, w, h):
    return Image.open(src).convert("RGB").resize((w,h),Image.Resampling.LANCZOS)

@lru_cache(maxsize=4096)
def _fit_size_lines(text, font_path, start_size, min_size, width, max_lines):
    for size in range(start_size, min_size-1, -2):
        f=_font(str(font_path), size)
        lines=wrap_words(text, f, width, strict=True)
        if lines is not None and len(lines)<=max_lines:
            return size, tuple(lines)
    f=_font(str(font_path), min_size)
    _l = wrap_words(text, f, width, strict=True)
    if _l is None: raise ValueError("caption has no clean line break at minimum size; reword or split the line")  # v2f: 語の途中で切らない
    if len(_l) > max_lines: raise ValueError("caption exceeds max lines at minimum size; reword or split the line")
    return min_size, tuple(_l)


def fit_text_words(text, font_path, start_size, min_size, width, max_lines):
    size, lines = _fit_size_lines(text, str(font_path), start_size, min_size, width, max_lines)
    return _font(str(font_path), size), list(lines)


@lru_cache(maxsize=8192)
def _caption_states(text, font_path, size, width, max_lines, available, gap, emphasis):
    """文字列は保持し、36px以上で収まる意味上の境界で字幕状態を分ける。"""
    def fits(value):
        try:
            font, lines = fit_text_words(value, font_path, size, size, width, max_lines)
            return len(lines) * (font.size + gap) <= available
        except ValueError:
            return False

    result = []
    for clause in re.findall(r'.+?(?:[。！？]+[」』）】]*|$)', text, flags=re.S):
        remaining = clause
        while not fits(remaining):
            protected = set()
            for term in emphasis:
                if not term:
                    continue
                offset = remaining.find(term)
                while offset >= 0:
                    protected.update(range(offset + 1, offset + len(term)))
                    offset = remaining.find(term, offset + 1)
            breaks = [i for i in phrase_breaks(remaining, tuple(emphasis)) if i not in protected
                      and fits(remaining[:i])]
            if not breaks:
                raise ValueError("36pxで字幕を安全に分割できない。台詞計画を確認する")
            cut = breaks[-1]
            result.append(remaining[:cut])
            remaining = remaining[cut:]
        result.append(remaining)
    return tuple(result)


def caption_states(text, font_path, layout, emphasis=()):
    _, y1, _, y2 = layout["zones"]["caption"]
    pad = int(layout.get("caption_box_pad", 14))
    available = y2-y1-2*pad-int(layout.get("caption_tab_height", 36))-int(layout.get("caption_tab_gap", 6))
    width = int(layout.get("caption_box_width", 760))-2*pad-24
    return _caption_states(text, str(font_path), max(36, int(layout["caption_font_size"])), width,
                           int(layout.get("caption_max_lines", 3)), available,
                           int(layout.get("caption_line_gap", 4)), tuple(emphasis))


def resize_character(im, target_h, opacity=1.0):
    work=im.copy()
    scale=target_h/work.height
    work=work.resize((max(1,int(work.width*scale)), target_h), Image.Resampling.LANCZOS)
    if opacity<1:
        work.putalpha(work.getchannel("A").point(lambda a:int(a*opacity)))
    return work


def draw_summary_block(d,layout,font_path,item):
    """中央パネル直下の要約（1〜2行、22px）。build_backgrounds と build_visual_layers（本番の経路）の両方が使う（2026-10-19）。"""
    if item.get("translation"): return
    _sum=summary_lines(item)
    if not _sum: return
    _sx1,_sy1,_sx2,_sy2=summary_box(layout["zones"]["evidence"],len(_sum))
    d.rounded_rectangle((_sx1,_sy1,_sx2,_sy2),radius=12,fill=layout["evidence_panel"],outline="#C9D5E3",width=2)
    _sf=_font(str(font_path),int(layout.get("summary_font_size",22)))
    for _i,_ln in enumerate(_sum): d.text((_sx1+14,_sy1+2+_i*SUMMARY_LINE_H),_ln,font=_sf,fill="#17202F")

def build_backgrounds(profile, presentation, presentation_path, font_path):
    layout=profile["layout"]; W,H=layout["width"],layout["height"]
    title_font=_font(str(font_path),layout["title_font_size"])
    small=_font(str(font_path),layout["source_font_size"])
    zones=layout["zones"]
    visuals=presentation.get("visuals") or [presentation]
    result={}; hashes={}
    for item in visuals:
        source_url=item.get("source_url") or presentation.get("source_url")
        source_credit=item.get("source_credit") or presentation.get("source_credit")
        if not source_url or not source_credit:
            raise ValueError("source-attributed visual requires source URL and credit")
        if item.get("media_region_only",presentation.get("media_region_only")) is not True and not _whole_post_source_visual(item):
            raise ValueError("whole-page capture requires explicit attributed source screenshot mode")
        src=Path(item["file"])
        if not src.is_absolute(): src=presentation_path.parent/src
        if not src.is_file(): raise FileNotFoundError(src)
        hashes[item["id"]]=hashlib.sha256(src.read_bytes()).hexdigest()

        bg=(_resized_bg(layout["background_image"],W,H).copy() if layout.get("background_image") else Image.new("RGB",(W,H),layout["background"]))
        d=ImageDraw.Draw(bg)
        hx1,hy1,hx2,hy2=zones["header"]
        d.rounded_rectangle((hx1,hy1,hx2,hy2),radius=16,fill=layout["header_background"])
        heading=str(item.get("title") or presentation["title"])
        _tx=layout.get("title_xy")
        _tp=tuple(_tx) if _tx else tuple(zones["title"])
        d.text(_tp,heading,font=title_font,fill=layout.get("header_text_color","#FFFFFF"),**({"anchor":"lm"} if _tx else {}))

        ex1,ey1,ex2,ey2=zones["evidence"]
        d.rounded_rectangle((ex1,ey1,ex2,ey2),radius=22,fill=layout["evidence_panel"],outline="#C9D5E3",width=2)
        visual=_contained_visual(str(src),ex2-ex1-34,ey2-ey1-34).copy()
        bg.paste(visual,(ex1+(ex2-ex1-visual.width)//2,ey1+(ey2-ey1-visual.height)//2))

        draw_summary_block(d,layout,font_path,item)  # 中央パネル直下の要約（visual_contract）
        pass  # diff: source credit not shown (RULES 2)
        pass  # diff: source URL not shown (RULES 2)
        if item.get("counter_label"):
            d.text((1230, 46), str(item["counter_label"]), font=_font(str(font_path),24), fill="#345577", anchor="rm")
        d.text(tuple(zones["voice_credit"]),str(presentation["voice_credit"]),font=small,fill="#F2F6FA")  # diff: legible on blackboard
        _rgb = _src_rgb(str(src)); _w, _h = _rgb.size
        _zc = float(item.get("zoom_crop", 0.12))  # FIX v1.5b: diagrams keep their text (crop <= 4%)
        _crop = _rgb.crop((int(_w*_zc), int(_h*_zc), int(_w*(1-_zc)), int(_h*(1-_zc))))
        bgz = bg.copy(); dz = ImageDraw.Draw(bgz)
        dz.rounded_rectangle((ex1,ey1,ex2,ey2),radius=22,fill=layout["evidence_panel"],outline="#C9D5E3",width=2)
        vz = ImageOps.contain(_crop,(ex2-ex1-34,ey2-ey1-34),Image.Resampling.LANCZOS)
        bgz.paste(vz,(ex1+(ex2-ex1-vz.width)//2,ey1+(ey2-ey1-vz.height)//2))
        result[item["id"]]=bg
        result[item["id"]+"__z"]=bgz  # FIX v1.5: zoomed framing for the 30-second visual rule
    return result,hashes


def _wrap_label(text, f, maxw):
    # 献立の項目名を、欄の幅で行に分ける（1 文字ずつ見て、はみ出したら次の行へ）
    lines=[]; cur=""
    for ch in text:
        if cur and f.getlength(cur+ch)>maxw and ch not in "）」』。、":  # 閉じ括弧や句読点だけが次の行に残らないように
            lines.append(cur); cur=ch
        else: cur+=ch
    lines.append(cur); return lines

def build_agenda_sidebar(cfg, font_path, W, H, supersample=1):
    """献立（右端の縦型）。導入・各章・まとめの状態ごとに一枚ずつ描く。
    章が終わると ✓ が付き、今の章は黄色で示す（2026-10-14 レビュー）。
    slide 設定がある時、導入では献立を真ん中に大きく出し、話の後に横へ弾いて右端へ移る（2026-10-14 依頼者の指示）。"""
    x1,y1,x2,y2=cfg["rect"]; w,h=x2-x1,y2-y1
    items=cfg["items"]; scenes=[it["scene"] for it in items]
    head_h=int(cfg.get("head_height",34)); row_h=int(cfg.get("row_height",46))
    SS=max(1,int(supersample))  # v20（2026-10-16）：SS 倍で描いてから縮める（線と文字の縁を滑らかにする）。1 なら従来どおり
    f_head=_font(str(font_path),int(cfg.get("head_font_size",17))*SS)
    f_item=_font(str(font_path),int(cfg.get("item_font_size",18))*SS)
    f_num=_font(str(font_path),int(cfg.get("num_font_size",16))*SS)
    slide=cfg.get("slide")
    slide_at=float(cfg.get("slide_at_sec",0.0)); slide_dur=float(slide.get("duration",0.8)) if slide else 0.0
    center_scale=float(slide.get("center_scale",1.25)) if slide else 1.0
    center_top=float(slide.get("center_top",y1)) if slide else y1
    # v13：項目名は、どれも欄に収まる同じ大きさにする（最も長い項目に合わせる）
    _maxw0=w*SS-(int(cfg.get("dot_x",28))+int(cfg.get("dot_r",13))+8)*SS-8*SS
    _lbl_size=12*SS  # 項目名は、どれも 2 行以内に収まる最大の大きさに揃える（18 から下げる）
    for _s in range(min(int(cfg.get("item_font_size",18)),15)*SS, 12*SS-1, -1):  # 2 行にしても行の高さに収まる大きさ（上限 15）
        _f=_font(str(font_path),_s)
        if all(len(_wrap_label(it["label"],_f,_maxw0))<=2 for it in items):
            _lbl_size=_s; break
    _f_lbl=_font(str(font_path),_lbl_size)
    cache={}
    def base_image(scene):
        if scene in cache: return cache[scene]
        if scene=="closing": done,current=set(scenes),None
        elif scene in scenes: done,current=set(scenes[:scenes.index(scene)]),scene
        else: done,current=set(),None
        K=SS  # 描画の倍率（v20、2026-10-16）：K 倍で描いて縮めると、線と文字の縁が滑らかになる
        im=Image.new("RGBA",(w*K,h*K),(0,0,0,0)); d=ImageDraw.Draw(im)
        d.rounded_rectangle((0,0,w*K-1,h*K-1),radius=16*K,fill="#FFFFFF",outline="#C9D5E3",width=2*K)
        d.text((w*K/2,head_h*K/2),cfg["title"],font=f_head,fill="#2B2B2B",anchor="mm")
        for n,it in enumerate(items):
            top=(head_h+n*row_h)*K; cy=top+row_h*K/2; cx=int(cfg.get("dot_x",28))*K; r=int(cfg.get("dot_r",13))*K
            if it["scene"]==current:
                d.rounded_rectangle((8*K,top+3*K,w*K-8*K,top+row_h*K-3*K),radius=12*K,fill="#FFFFFF",outline="#2E9E6A",width=3*K)
            if it["scene"] in done:
                d.ellipse((cx-r,cy-r,cx+r,cy+r),fill="#2E9E6A")
                d.line([(cx-6*K,cy),(cx-2*K,cy+5*K),(cx+6*K,cy-5*K)],fill="#FFFFFF",width=3*K)
                color="#5B6472"
            elif it["scene"]==current:
                d.ellipse((cx-r,cy-r,cx+r,cy+r),fill="#FFFFFF",outline="#2E9E6A",width=3*K)
                d.text((cx,cy),str(n+1),font=f_num,fill="#1F6F4D",anchor="mm")
                color="#1F6F4D"
            else:
                d.ellipse((cx-r,cy-r,cx+r,cy+r),fill="#FFFFFF",outline="#9AA5B1",width=2*K)
                d.text((cx,cy),str(n+1),font=f_num,fill="#7A8594",anchor="mm")
                color="#7A8594"
            _ls=_wrap_label(it["label"],_f_lbl,_maxw0)  # v13：長い項目名は 2 行に分ける
            for _j,_ln in enumerate(_ls):
                d.text((cx+r+8*K,cy+(_j-(len(_ls)-1)/2)*_f_lbl.size*1.15),_ln,font=_f_lbl,fill=color,anchor="lm")
        if K>1: im=im.resize((w,h),Image.Resampling.LANCZOS)
        cache[scene]=im
        return im
    def pose(t,scene):
        """（拡大率, 左, 上, 回転角）。導入の移動の前は真ん中、移動の間は弾みと揺れ、移動の後は右端。"""
        if not slide or scene is None: return (1.0,x1,y1,0.0)
        x0=(W-w*center_scale)/2
        if t<slide_at: return (center_scale,round(x0),round(center_top),0.0)
        if t>=slide_at+slide_dur: return (1.0,x1,y1,0.0)
        p=(t-slide_at)/slide_dur; e=1-(1-p)**3
        x=x0+(x1-x0)*e+6*math.sin(2*math.pi*1.5*p)*(1-p)
        y=center_top+(y1-center_top)*e
        s=center_scale+(1.0-center_scale)*e
        return (round(s,3),round(x),round(y),round(8*math.sin(2*math.pi*2*p)*(1-p),2))
    def state_image(scene,po):
        im=base_image(scene)
        s,x,y,ang=po
        if s!=1.0: im=im.resize((max(1,round(w*s)),max(1,round(h*s))),Image.Resampling.LANCZOS)
        if ang: im=im.rotate(ang,resample=Image.Resampling.BICUBIC,expand=True)
        return im,(x,y)
    return state_image,pose


SWITCH_DUR=1.9  # v11：画像の切り替えの動き（秒）。キャラクターのひょっこりと、見出しの帯
def draw_switch_overlay(frame,d,t,switches,font,W):
    if not PEEK_ENABLED: return  # R45 廃止（2026-10-19）：顔出しは描かない
    for k,(s0,head,card) in enumerate(switches):
        p=t-s0
        if p<0 or p>=SWITCH_DUR: continue
        name="ずんだもん" if k%2==0 else "四国めたん"
        im=_CARD_CHARS.get(name)
        if im is not None:
            A=min(1.0,p/0.35) if p<0.35 else (1.0 if p<SWITCH_DUR-0.5 else (SWITCH_DUR-p)/0.5)
            hh=int(im.height*0.55)
            top=im.crop((0,0,im.width,hh))
            x=24 if name=="ずんだもん" else 1040-im.width  # 献立（右の進行表示）の左に出す
            frame.paste(top,(x,int(-(1-A)*hh)),top)
        # 見出しの帯は出さない（画面の上の見出しに、同じ主題が出るため）

def draw_translation(d,layout,text,font):
    x1,y1,x2,y2=layout["zones"]["evidence"]
    d.rectangle((x1,y2-36,x2,y2),fill="#FFFFFF",outline="#2B6146",width=2)
    d.text((x1+12,y2-18),text,font=font,fill="#17202F",anchor="lm")

def draw_caption_compact(d,W,layout,font_path,speaker,caption,emph=()):
    """字幕の板（コンパクト版、2026-10-14 依頼者の指示）。名前は板の外（上）に出し、名前の中で文字を中央に置く。
    板の大きさは固定（三行が余裕を持って入る）。字の大きさは 24 px を下回らない（RB-48）。"""
    x1,y1,x2,y2=layout["zones"]["caption"]
    accent=layout["caption_colors"][speaker]
    box_w=int(layout.get("caption_box_width",760)); pad=int(layout.get("caption_box_pad",14))
    tab_h=int(layout.get("caption_tab_height",36)); tab_gap=int(layout.get("caption_tab_gap",6))
    max_lines=int(layout.get("caption_max_lines",3)); gap=int(layout.get("caption_line_gap",4))
    text_w=box_w-2*pad-24; avail=(y2-y1)-2*pad-tab_h-tab_gap
    size=int(layout["caption_font_size"])
    while True:
        f,lines=fit_text_words(caption,font_path,size,36,text_w,max_lines)
        if len(lines)*(f.size+gap)<=avail or f.size<=36: break
        size=f.size-2
    if f.size<36: raise ValueError("caption would shrink below 36 px (R13): %s" % caption[:20])
    if len(lines)*(f.size+gap)>avail: raise ValueError("compact caption would clip outside the caption area")
    f_tab=_font(str(font_path),int(layout.get("caption_tab_font_size",22)))
    tab_w=int(f_tab.getlength(speaker))+36
    board_h=(y2-y1)-tab_h-tab_gap  # 板の大きさは固定（依頼者の指示 2026-10-14）：三行が余裕を持って入る高さ
    bx1=(W-box_w)//2; bx2=bx1+box_w; by2=y2; by1=by2-board_h
    d.rounded_rectangle((bx1,by1,bx2,by2),radius=16,fill=layout.get("caption_backplates",{}).get(speaker,layout["caption_backplate"]),outline=layout.get("caption_board_outline",{}).get(speaker,accent),width=int(layout.get("caption_board_outline_width",4)))
    ty2=by1-tab_gap; ty1=ty2-tab_h; tx1=bx1+18
    d.rounded_rectangle((tx1,ty1,tx1+tab_w,ty2),radius=tab_h//2,fill=accent)
    d.text((tx1+tab_w/2,(ty1+ty2)/2),speaker,font=f_tab,fill="#17202F",anchor="mm")
    text_h=len(lines)*(f.size+gap)-gap
    yy=by1+(board_h-text_h)/2  # 文字は板の中で上下中央
    _cbase=layout["caption_text_colors"][speaker]
    _ch=_cbase.lstrip("#"); _cdark=tuple(int(int(_ch[i:i+2],16)*0.8) for i in (0,2,4))  # 字幕の色より一段階濃い（濃すぎない）
    for line in lines:
        x0=(W-f.getlength(line))/2
        for t in emph:  # 重要な文のマーカー：淡い黄の帯を文字の下に敷く（R42）
            k=line.find(t)
            while k>=0:
                px=x0+f.getlength(line[:k]); pw=f.getlength(t)
                d.rectangle((px-3,yy+f.size*0.10,px+pw+3,yy+f.size*0.98),fill=layout.get("caption_marker_color","#FFE98A"))
                k=line.find(t,k+len(t))
        d.text((x0,yy),line,font=f,fill=_cbase)
        for t in emph:  # 重要な語だけ、一段階濃い色で描く（台本の caption_emphasis_terms）
            k=line.find(t)
            while k>=0:
                px=x0+f.getlength(line[:k])
                d.text((px,yy),t,font=f,fill=_cdark)
                k=line.find(t,k+len(t))
        yy+=f.size+gap

def visual_motion(vis_t, perf):
    """場面の動き（v20、2026-10-16）。参考動画の計測に合わせる：入りだけ動く（既定 1.5 秒。少し小さい所から戻り、薄い所からはっきり出す）。
    その後は止まる。止まりが長い時は、場面の始まりから一定の間隔で小さく寄せる（既定 10 秒ごとに 2.5 秒、2 %）。戻り値は（拡大率, 不透明度）。"""
    entry=float(perf.get("visual_entry_seconds",1.5))
    period=max(1.0,float(perf.get("visual_beat_seconds",10)))
    nudge=float(perf.get("visual_nudge_seconds",2.5))
    amp=float(perf.get("visual_nudge_scale",0.02))
    if vis_t<0: return 1.0,1.0
    if vis_t<entry:
        e=1-(1-vis_t/entry)**3
        return round(0.965+0.035*e,4),round(min(1.0,e/0.4),3)
    if vis_t>=period and vis_t%period<nudge:
        return round(1+amp*math.sin(math.pi*(vis_t%period)/nudge),4),1.0
    return 1.0,1.0


def chapter_card_at(cards, t_abs):
    """全編の時刻 t_abs にある章カード。カードの外なら None。"""
    for c in cards:
        if c["start"]<=t_abs<c["end"]: return c
    return None


_CARD_CHARS = {}

def _prep_card_char(vd, target_h=280):
    # 章カード用のキャラクター（HAPPY か NORMAL の口閉じ）を一度だけ縮小する
    key = ("HAPPY", 0) if ("HAPPY", 0) in vd else (("NORMAL", 0) if ("NORMAL", 0) in vd else next(iter(vd)))
    return resize_character(vd[key], target_h, 1.0)

def draw_chapter_card(card, elapsed, cfg, font_path, W, H):
    """章カード（v22、2026-10-16）：章の挨拶の声の時間に合わせて出す。章ごとに遊び心のある動き（play）を 1 つ持つ。
    1 投げて中央に（放物線、着地で弾む）、2 左からぴょこぴょこ跳ねて、3 左右から寄せてカチッと、4 上から木の葉のように揺れて降りる、
    5 カードが回って現れる＋緑の帯が風に舞って通り抜ける、6 スタンプのように押される。色は profile の chapter_card に従う。話し声は無い（挨拶が声になる）。"""
    import math
    def clamp(x): return min(1.0, max(0.0, x))
    def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
    def ease_back(x):
        x = clamp(x); c1 = 1.70158; c3 = c1 + 1
        return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
    def rgba(color, alpha):
        return ImageColor.getrgb(color) + (int(round(255 * clamp(alpha))),)
    e = elapsed; n, N = int(card["index"]), int(card["total"]); play = card.get("play", "stamp")
    cx, cy = W // 2, H // 2 - 40
    base = Image.new("RGBA", (W, H), rgba(cfg.get("bg", "#EEF7F1"), 1.0))
    if play == "ribbon_flip" and 0.05 < e < 2.2:  # 緑の帯が風に舞って、画面を横切る
        band = Image.new("RGBA", (W, H), (0, 0, 0, 0)); bd = ImageDraw.Draw(band)
        fade = min(1.0, (e - 0.05) / 0.25) * min(1.0, (2.2 - e) / 0.5)
        top, bot = [], []
        for x in range(-40, W + 41, 8):
            ph = x / 150.0 + e * 6.0
            yc = H * 0.5 + 70 * math.sin(ph) + 25 * math.sin(ph * 2.3)
            th = 38 + 16 * math.cos(ph * 1.7)
            top.append((x, yc - th / 2)); bot.append((x, yc + th / 2))
        bd.polygon(top + bot[::-1], fill=rgba(cfg.get("bar_done", "#71C7A5"), 0.55 * fade))
        base = Image.alpha_composite(base, band)
    # カード（札・番号・章名を 1 枚の絵にする）
    f_pill = _font(str(font_path), int(cfg.get("pill_font_size", 24)))
    f_num = _font(str(font_path), int(cfg.get("number_font_size", 118)))
    f_label = _font(str(font_path), int(cfg.get("label_font_size", 44)))
    text = f"CHAPTER {n} / {N}"; tw = int(f_pill.getlength(text)) + 56
    spr = Image.new("RGBA", (760, 300), (0, 0, 0, 0)); sd = ImageDraw.Draw(spr)
    sd.rounded_rectangle((380 - tw // 2, 10, 380 + tw // 2, 58), radius=24, fill=rgba(cfg.get("pill", "#2B6146"), 1.0))
    sd.text((380, 34), text, font=f_pill, fill=(255, 255, 255, 255), anchor="mm")
    sd.text((380, 150), f"第{n}章", font=f_num, fill=rgba(cfg.get("number", "#1F4D36"), 1.0), anchor="mm")
    sd.text((380, 246), str(card["label"]), font=f_label, fill=rgba(cfg.get("label", "#3E7A5A"), 1.0), anchor="mm")
    x, y, sx, sy, ang, alpha = cx, cy, 1.0, 1.0, 0.0, 1.0
    if play == "throw_to_center":
        T = 0.9; p = clamp(e / T)
        x = (W + 250) + (cx - (W + 250)) * ease_out(p); y = (H + 150) + (cy - (H + 150)) * p - 260 * math.sin(math.pi * p)
        ang = 28 * (1 - ease_out(p)) * math.cos(p * 9)
        if e > T: sx = sy = 1 + math.sin((e - T) * 20) * math.exp(-(e - T) * 7) * 0.07
        alpha = clamp(e / 0.12)
    elif play == "hop_in":
        T = 1.5; p = clamp(e / T); k = p * 3; frac = k - math.floor(k)
        x = -300 + (cx + 300) * ease_out(p); y = cy - 90 * abs(math.sin(math.pi * frac)) if p < 1 else cy
        if frac < 0.12 or p >= 1: sy = 0.94; sx = 1.04
        alpha = clamp(e / 0.15)
    elif play == "two_cards_snap":
        T = 0.8; p = clamp(e / T)
        x = (W + 300) + (cx - (W + 300)) * ease_back(p)
        if e > 0.8: y = cy + 4 * math.sin((e - 0.8) * 30) * math.exp(-(e - 0.8) * 9)
        alpha = clamp(e / 0.1)
    elif play == "drop_leaf":
        T = 1.0; p = clamp(e / T)
        y = -320 + (cy + 320) * (1 - (1 - p) ** 2) + (14 * math.sin(p * math.pi * 2) * (1 - p) if p < 1 else 0)
        ang = 14 * math.sin(e * 5) * (1 - p)
        if p >= 1: sy = 1 + math.sin((e - T) * 16) * math.exp(-(e - T) * 8) * 0.05
        alpha = clamp(e / 0.2)
    elif play == "ribbon_flip":
        p = clamp((e - 0.3) / 0.6)
        sx = max(0.04, math.sin(p * math.pi / 2)); alpha = clamp((e - 0.2) / 0.2)
    else:  # stamp
        p = clamp(e / 0.25); sx = sy = 1.6 - 0.6 * ease_out(p); alpha = p
    if sx > 0.01 and sy > 0.01:
        img = spr.rotate(ang, resample=Image.Resampling.BICUBIC, expand=True) if abs(ang) > 0.01 else spr
        w2, h2 = max(1, int(img.width * sx)), max(1, int(img.height * sy))
        img = img.resize((w2, h2), Image.Resampling.LANCZOS)
        a = img.getchannel("A").point(lambda v: int(v * clamp(alpha)))
        img.putalpha(a)
        base.alpha_composite(img, (int(round(x - w2 / 2)), int(round(y - h2 / 2))))
    # キャラクター（章ごとに、左右から覗く・跳ねる・お辞儀をする）。字幕、札、中央の番号は隠さない（下端と左右の縁だけ）
    def put_char(name, side, p_in, bob, alpha_v, flip=False):
        if not CHAPTER_CARD_CHARACTERS: return  # 2026-10-19：章カードに人物を出さない
        im = _CARD_CHARS.get(name)
        if im is None or alpha_v <= 0.01: return
        if flip: im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        w_, h_ = im.width, im.height
        frac = 0.42  # 見える割合（縁から覗く）
        if side == "right":
            x_left = W - w_ * frac + (1 - ease_out(p_in)) * w_ * 0.7
        else:
            x_left = -w_ * (1 - frac) - (1 - ease_out(p_in)) * w_ * 0.7
        y_bottom = H - 26 + bob
        a = im.getchannel("A").point(lambda v: int(v * clamp(alpha_v)))
        im2 = im.copy(); im2.putalpha(a)
        base.alpha_composite(im2, (int(round(x_left)), int(round(y_bottom - h_))))
    if play == "throw_to_center":
        put_char("ずんだもん", "right", clamp(e / 0.35), 6 * math.sin(e * 9) * math.exp(-e * 2), clamp(e / 0.2))
    elif play == "hop_in":
        put_char("四国めたん", "left", clamp(e / 0.35), -10 * abs(math.sin(e * 7.5)), clamp(e / 0.2))
    elif play == "two_cards_snap":
        put_char("ずんだもん", "right", clamp(e / 0.4), -6 * abs(math.sin(e * 6)), clamp(e / 0.2))
        put_char("四国めたん", "left", clamp(e / 0.4), -6 * abs(math.sin(e * 6 + 1.2)), clamp(e / 0.2))
    elif play == "drop_leaf":
        put_char("ずんだもん", "right", clamp(e / 0.5), 4 * math.sin(e * 3), clamp(e / 0.3), flip=True)
    elif play == "ribbon_flip":
        put_char("四国めたん", "right", clamp(e / 0.4), -8 * abs(math.sin(e * 5)), clamp(e / 0.2), flip=True)
    else:  # stamp
        put_char("ずんだもん", "left", clamp(e / 0.3), -12 * abs(math.sin(e * 8)) * (1 if e < 1.2 else 0), clamp(e / 0.15))
        put_char("四国めたん", "right", clamp(e / 0.3), -12 * abs(math.sin(e * 8 + 0.8)) * (1 if e < 1.2 else 0), clamp(e / 0.15))
    return base.convert("RGB")

def build_visual_layers(profile, presentation, presentation_path, font_path):
    """場面ごとに、板（背景・見出し・声の表示）と、図の枠（白い枠と図）を分けて作る（v20）。枠だけを動かすため。
    枠の位置と大きさは従来の描画と同じ。戻り値は {場面 id: (板, 枠の RGBA, (x, y))}。"""
    layout=profile["layout"]; W,H=layout["width"],layout["height"]
    title_font=_font(str(font_path),layout["title_font_size"])
    small=_font(str(font_path),layout["source_font_size"])
    zones=layout["zones"]; ex1,ey1,ex2,ey2=zones["evidence"]
    pw,ph=ex2-ex1+1,ey2-ey1+1
    out={}
    for item in (presentation.get("visuals") or [presentation]):
        src=Path(item["file"])
        if not src.is_absolute(): src=presentation_path.parent/src
        bg=(_resized_bg(layout["background_image"],W,H).copy() if layout.get("background_image") else Image.new("RGB",(W,H),layout["background"]))
        d=ImageDraw.Draw(bg)
        hx1,hy1,hx2,hy2=zones["header"]
        d.rounded_rectangle((hx1,hy1,hx2,hy2),radius=16,fill=layout["header_background"])
        heading=str(item.get("title") or presentation["title"])
        _tx=layout.get("title_xy")
        _tp=tuple(_tx) if _tx else tuple(zones["title"])
        d.text(_tp,heading,font=title_font,fill=layout.get("header_text_color","#FFFFFF"),**({"anchor":"lm"} if _tx else {}))
        if item.get("counter_label"):
            d.text((1230, 46), str(item["counter_label"]), font=_font(str(font_path),24), fill="#345577", anchor="rm")
        d.text(tuple(zones["voice_credit"]),str(presentation["voice_credit"]),font=small,fill="#F2F6FA")
        draw_summary_block(d,layout,font_path,item)  # 本番の経路（layered）でも要約を描く
        panel=Image.new("RGBA",(pw,ph),(0,0,0,0)); pd=ImageDraw.Draw(panel)
        pd.rounded_rectangle((0,0,pw-1,ph-1),radius=22,fill=layout["evidence_panel"],outline="#C9D5E3",width=2)
        visual=_contained_visual(str(src),ex2-ex1-34,ey2-ey1-34).copy()
        panel.paste(visual.convert("RGBA"),((ex2-ex1-visual.width)//2,(ey2-ey1-visual.height)//2))
        out[item["id"]]=(bg,panel,(ex1,ey1))
    return out


def panel_for(layers, vid, scale, alpha, cache):
    """枠を、拡大率と不透明度に合わせて作る（同じ組は一度だけ作る）。"""
    key=(vid,round(scale,4),round(alpha,3))
    if key in cache:
        cache.move_to_end(key); return cache[key]
    _bg,panel,(ex1,ey1)=layers[vid]
    pw,ph=panel.size
    img=panel if scale==1.0 else panel.resize((max(1,round(pw*scale)),max(1,round(ph*scale))),Image.Resampling.LANCZOS)
    if alpha<1.0:
        img=img.copy(); img.putalpha(img.getchannel("A").point(lambda v:int(v*alpha)))
    pos=(ex1+(pw-img.width)//2,ey1+(ph-img.height)//2)
    cache[key]=(img,pos)
    while len(cache)>64: cache.popitem(last=False)
    return img,pos


def visual_origin(all_records, t_abs, default_visual):
    """全編の時刻 t_abs にある場面と、その場面が始まった全編の時刻（区間の描画で使う、v20）。"""
    cur = None
    started = 0.0
    for r in sorted(all_records, key=lambda x: x["start"]):
        if r["start"] > t_abs + 1e-9:
            break
        vid = r.get("visual_id", default_visual)
        if vid != cur:
            cur = vid
            started = r["start"]
    return (cur or default_visual), started


def scene_origin(all_records, t_abs):
    """全編の時刻 t_abs で、直前に話していた台詞の章（献立の今の章、区間の描画で使う、v20）。"""
    scene = None
    for r in sorted(all_records, key=lambda x: x["start"]):
        if r["start"] > t_abs + 1e-9:
            break
        scene = r.get("scene_id")
    return scene


def render(args):
    render_started=time.monotonic()
    if getattr(args, "preview", False) and not 1 <= args.duration <= 2:
        raise ValueError("previewは1〜2秒だけ")
    profile=json.loads(args.profile.read_text(encoding="utf-8"))
    presentation=json.loads(args.presentation.read_text(encoding="utf-8"))
    layout=profile["layout"]; acting=profile["acting"]
    if (layout["width"],layout["height"])!=(1280,720):
        raise ValueError("approved speed-first landscape canvas must be 1280x720")
    if layout["caption_colors"]!={"ずんだもん":"#B8E6C8","四国めたん":"#F2C4D7"}:
        raise ValueError("approved pale speaker palette required")
    for field in ("title","source_credit","source_url","voice_credit"):
        if not presentation.get(field): raise ValueError(f"missing presentation field: {field}")

    timing=json.loads(args.timing.read_text(encoding="utf-8"))
    verified_motion_timeline=bound_timeline(presentation,timing["records"],args.presentation.parent,profile=profile)
    records=[{**r,"start":r["start"]-args.start,"end":r["end"]-args.start}
             for r in timing["records"] if r["end"]>args.start and r["start"]<args.start+args.duration]
    if not records: raise ValueError("no measured dialogue records in requested range")

    with wave.open(str(args.audio)) as w:
        if w.getnchannels()!=1 or w.getsampwidth()!=2:
            raise ValueError("mono PCM16 narration required")
        rate=w.getframerate()
        samples=array("h",w.readframes(w.getnframes()))
    if len(samples)/rate+0.001<args.start+args.duration:
        raise ValueError("narration shorter than requested duration")

    variants={}; char_keys={}
    for speaker,char in (("ずんだもん","Zundamon"),("四国めたん","Metan")):
        variants[speaker],char_keys[speaker]=character_variants(args.shell/char,char,args.cache_root or args.output.parent/"reusable-assets")
        _CARD_CHARS[speaker]=_prep_card_char(variants[speaker])

    backgrounds,visual_hashes=build_backgrounds(profile,presentation,args.presentation,args.font)
    default_visual=next(iter(backgrounds))
    perf_cfg=profile.get("performance") or {}
    layered=bool(perf_cfg.get("visual_entry_seconds"))  # v20（2026-10-16）：入りの動きと章カード。値が無い版は従来の描画のまま
    layers=build_visual_layers(profile,presentation,args.presentation,args.font) if layered else None
    motion_timeline=verified_motion_timeline or {}
    chapter_cards=presentation.get("chapter_cards") or []
    production_cues=presentation.get('production_cues') or {}
    card_cfg=profile.get("chapter_card") or {}
    for r in records:
        if r["speaker"] not in variants: raise ValueError("unsupported speaker")
        if r.get("emotion","NORMAL") not in acting["expressions"]: raise ValueError("unsupported authored emotion")
        if r.get("visual_id",default_visual) not in backgrounds: raise ValueError("unknown visual_id")

    for _v in (presentation.get("visuals") or []):
        _spoken=[x["caption_text"] for x in timing["records"] if x.get("caption_text")]  # 全編のナレーションと比べる
        _errs=check_summary(_v.get("summary"),_spoken)
        if _errs: raise ValueError("中央パネルの表示契約違反 %s: %s" % (_v.get("id"), "; ".join(_errs)))
    sidebar_cfg=presentation.get("agenda_sidebar")  # 献立（右端の縦型）。設定が無い動画には出さない
    sidebar_state,sidebar_pose=build_agenda_sidebar(sidebar_cfg,args.font,layout["width"],layout["height"],supersample=int((profile.get("performance") or {}).get("agenda_supersample",1))) if sidebar_cfg else (None,None)
    sidebar_seen=set()
    W,H,FPS=layout["width"],layout["height"],layout["fps"]
    caption_font=ImageFont.truetype(str(args.font),layout["caption_font_size"])
    label_font=ImageFont.truetype(str(args.font),int(layout.get("source_label_font_size",12)))
    _src_label={v["id"]:v.get("source_label") for v in (presentation.get("visuals") or [])}
    small=ImageFont.truetype(str(args.font),layout["source_font_size"])
    _band_font=ImageFont.truetype(str(args.font),int(layout.get("band_font_size",20)))  # v11：見出しの帯と訳の帯
    _cards_l=[(c["start"]-args.start,c["end"]-args.start) for c in (presentation.get("chapter_cards") or [])]
    _secname={x["id"]:x.get("heading") for x in (presentation.get("sections") or [])}
    _sw=[]; _prev=None
    for r0 in sorted(records,key=lambda x:x["start"]):  # 画像の切り替えの一覧（章カードの中なら、カードの終わり）
        if _prev is not None and r0.get("visual_id")!=_prev.get("visual_id"):
            st=r0["start"]; card=False
            for s0,s1 in _cards_l:
                if s0<=st<s1: st=s1; card=True
            head=_secname.get(r0.get("scene_id")) if r0.get("scene_id")!=(_prev or {}).get("scene_id") else None
            _sw.append((st,head,card))
        _prev=r0
    out=args.output; out.parent.mkdir(parents=True,exist_ok=True)
    audio_fast=profile.get("audio_fast_path") or {}
    gain_db=float(audio_fast.get("fixed_gain_db",0.0))
    limiter=float(audio_fast.get("true_peak_limiter_linear",0.84))
    audio_filter=f"volume={gain_db}dB,alimiter=limit={limiter}:level=disabled" if gain_db else f"alimiter=limit={limiter}:level=disabled"  # FIX v1.4: alimiter level=1 raised loudness by ~8 dB
    _RATE=(["-crf",os.environ["OUT_CRF"]] if os.environ.get("OUT_CRF") else ["-b:v","144k","-maxrate","168k","-bufsize","288k"])  # v10：画質は CRF で指定できる（既定は従来の 144 kbps）
    if getattr(args, "video_only", False) and os.environ.get("DIRECT_ENCODE")=="1":  # 一本の直接符号化：中間の yuv を書かない（音声は後で付ける）
        cmd=["ffmpeg","-v","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","pipe:0",
             "-vf",os.environ.get("OUT_VF","scale=640:360:flags=lanczos"),"-c:v","libx264","-preset",os.environ.get("OUT_PRESET","ultrafast"),"-threads",os.environ.get("DIRECT_THREADS","8"),  # 1 で再現する（8 だと実行ごとに映像が変わる。描画時間は同じ）
             *_RATE,"-pix_fmt","yuv420p","-an",str(out)]
    elif getattr(args, "video_only", False):  # 区間の描画（v20）：映像だけ。切れ目は鍵フレーム（連結は -c copy）
        cmd=["ffmpeg","-v","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","pipe:0",
             "-vf","scale=640:360:flags=lanczos"]+(["-c:v","rawvideo","-pix_fmt","yuv420p","-f","rawvideo"] if os.environ.get("RAW_INTERMEDIATE")=="1" else ["-c:v","ffv1","-level","3","-pix_fmt","yuv420p"])+["-an",str(out)]
    else:
        cmd=["ffmpeg","-v","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","pipe:0",
             "-ss",str(args.start),"-i",str(args.audio),"-vf","scale=640:360:flags=lanczos","-c:v","libx264","-preset","medium","-threads","2","-b:v","144k","-maxrate","168k","-bufsize","288k","-pix_fmt","yuv420p",
             "-af",audio_filter,"-c:a","aac","-b:a","48k","-ar","48000","-movflags","+faststart","-shortest",str(out)]
    character_positions={}; character_frame_evidence={}
    translation_cards = TranslationCards(presentation.get("visuals", []), layout, args.font)
    video_panels = VideoPanels(presentation.get("visuals", []))
    with closing(video_panels), encoder_process(cmd, flush_timeout=None) as process:

        resized={}; mouth_seen={k:set() for k in variants}; expr_seen={k:set() for k in variants}
        performance=profile.get("performance") or {}
        mouth_hz=max(1.0,float(performance.get("mouth_update_hz",10)))
        mouth_step=max(1,int(round(FPS/mouth_hz)))
        cache_limit=max(1,int(performance.get("frame_state_cache_entries",24)))
        beat_s=max(1.0,float(performance.get("visual_beat_seconds",25)))  # FIX v1.5: framing switch interval
        visual_started=0.0
        frame_cache=OrderedDict()
        cache_hits=0; cache_misses=0
        panel_cache=OrderedDict(); cards_drawn=0
        current_visual=(records[0].get("visual_id",default_visual) if layered else default_visual)
        if layered and args.start>0:  # 区間の途中から（v20）：場面の入りは全編の時刻で数える
            current_visual,_st=visual_origin(timing["records"],args.start,default_visual)
            visual_started=_st-args.start
        record_idx=0
        mouth=0
        if args.start>0:  # 区間の先頭の口の状態は、直前の更新時刻（全編の時刻）の値を使う（一本の描画と一致させる）
            _k0=(int(round(args.start*FPS))//mouth_step)*mouth_step
            _c0=samples[int((_k0/FPS)*rate):int((_k0/FPS+acting["window_seconds"])*rate)]
            _r0=math.sqrt(sum(x*x for x in _c0)/len(_c0))/32768 if _c0 else 0
            mouth=0 if _r0<acting["closed_threshold"] else (1 if _r0<acting["wide_threshold"] else 2)
        last_sb_scene=((scene_origin(timing["records"],args.start) or records[0].get("scene_id")) if args.start>0 else records[0].get("scene_id")) if (sidebar_state and records) else None  # 献立は、話している行の章に合わせる（見出し・背景と同じ時刻）
        for i in range(render_frame_count(len(samples), rate, FPS, args.start, args.duration)):
            t=i/FPS
            while record_idx+1<len(records) and t>=records[record_idx]["end"]:
                record_idx += 1
            _card=chapter_card_at(chapter_cards,t+args.start) if chapter_cards else None
            if _card is not None:  # 章カード（話していない時間）。字幕・人物・献立は出さない（v20、2026-10-16）
                process.stdin.write(draw_chapter_card(_card,t+args.start-_card["start"],card_cfg,args.font,layout["width"],layout["height"]).tobytes())
                cards_drawn += 1
                continue
            candidate=records[record_idx] if record_idx<len(records) else None
            r=candidate if candidate and candidate["start"]<=t<candidate["end"] else None
            if r:
                _vid=r.get("visual_id",default_visual)
                if _vid!=current_visual: visual_started=t
                current_visual=_vid
            speaker=r["speaker"] if r else None
            expression=r.get("emotion","NORMAL") if r else "NORMAL"

            if not r:
                mouth=0
            elif (i + int(round(args.start*FPS))) % mouth_step == 0:  # 全編の時刻で位相を決める（区間の先頭に依存させない）
                _ta=(int(round(args.start*FPS))+i)/FPS  # 全編の時刻（フレーム番号から。丸めた開始時刻を使わない）
                chunk=samples[int(_ta*rate):int((_ta+acting["window_seconds"])*rate)]
                rms=math.sqrt(sum(x*x for x in chunk)/len(chunk))/32768 if chunk else 0
                mouth=0 if rms<acting["closed_threshold"] else (1 if rms<acting["wide_threshold"] else 2)

            caption=""
            if r:
                clauses=(caption_states(r["caption_text"], args.font, layout, r.get("caption_emphasis_terms") or ())
                         if layout.get("caption_layout") == "compact_v18"
                         else [x for x in re.split(r"(?<=[。！？])",r["caption_text"]) if x])
                lengths=[len(x) for x in clauses]
                pos=(t-r["start"])/max(0.001,r["end"]-r["start"])*sum(lengths)
                acc=0; caption=clauses[-1]
                for c in clauses:
                    acc+=len(c)
                    if pos<acc:
                        caption=c
                        break

            char_states=[]
            for name,side in (("ずんだもん","left"),("四国めたん","right")):  # FIX v1.4: ずんだもん left, 四国めたん right
                active=name==speaker
                state=mouth if active else 0
                expr=expression if active else ("SERIOUS" if expression=="SERIOUS" else "NORMAL")
                if active and expression=="HAPPY" and r and t-r["start"]>acting["happiness_hold_seconds"]:
                    expr="NORMAL"
                mouth_seen[name].add(state); expr_seen[name].add(expr)
                char_states.append((name,side,active,state,expr))

            if layered:
                _ms,_ma=visual_motion(t-visual_started,perf_cfg)
                beat_key=(current_visual,_ms,_ma)
            else:
                _beat=int((t-visual_started)//beat_s)
                beat_key=current_visual if _beat%2==0 else current_visual+"__z"  # FIX v1.5: switch framing every beat (max static 25 s)
            bf=max(0.0,1-(t-r["start"])/0.4) if (r and 0<=t-r["start"]<0.4) else 0.0  # FIX v1.4 (ADD-10): speech-start bounce factor
            bump=int(round(100*bf))  # cache key for bounce state
            if r: last_sb_scene=r.get("scene_id")  # 話している行の章。候補（次の行）は使わない（v2e、2026-10-15）
            sb_scene=last_sb_scene if sidebar_state else None
            sb_pose=sidebar_pose(t+args.start,sb_scene) if sb_scene is not None else None
            translation_state = translation_cards.state(current_visual, t-r["start"] if r else 0, r["end"]-r["start"] if r else 1)
            frame_key=(translation_state,beat_key,speaker,expression,mouth,caption,tuple((x[0],x[2],x[3],x[4]) for x in char_states),bump,sb_scene,sb_pose)
            frame_key += (motion_cache_state(motion_timeline,t+args.start,FPS),)
            frame_key += (direction_cache_state(production_cues,t+args.start),)
            if current_visual in video_panels.paths:
                frame_key += (i,)
            cached=frame_cache.get(frame_key)
            if cached is not None:
                cache_hits += 1
                frame_cache.move_to_end(frame_key)
                process.stdin.write(cached)
                continue

            cache_misses += 1
            if layered:
                frame=layers[current_visual][0].copy()
                _pimg,_pxy=panel_for(layers,current_visual,beat_key[1],beat_key[2],panel_cache)
                video_frame = video_panels.frame(current_visual, t-visual_started)
                if video_frame is not None:
                    panel = layers[current_visual][1].copy()
                    picture = ImageOps.contain(video_frame, (panel.width-34, panel.height-34), Image.Resampling.LANCZOS)
                    panel.paste(picture, ((panel.width-picture.width)//2, (panel.height-picture.height)//2))
                    scale, alpha = beat_key[1], beat_key[2]
                    _pimg = panel.resize((max(1,round(panel.width*scale)),max(1,round(panel.height*scale))), Image.Resampling.LANCZOS)
                    if alpha < 1:
                        _pimg.putalpha(_pimg.getchannel("A").point(lambda value:int(value*alpha)))
                frame.paste(_pimg,_pxy,_pimg); d=ImageDraw.Draw(frame)
            else:
                frame=backgrounds[beat_key].copy(); d=ImageDraw.Draw(frame)
            translation_cards.draw(frame, translation_state)
            motion = active_diagram(motion_timeline,t+args.start)
            if motion is not None and presentation.get('motion_in_visual_slot', MOTION_TEXT_IN_VISUAL_SLOT):
                ex1,ey1,ex2,ey2=presentation.get('motion_region', layout['zones']['evidence'])
                panel=render_motion_panel(motion,t+args.start,(ex2-ex1,ey2-ey1),args.font,
                                          int(perf_cfg.get('diagram_supersample',4)))
                frame.paste(panel,(ex1,ey1)); d=ImageDraw.Draw(frame)
            draw_direction(frame,production_cues,t+args.start,args.font)
            if sb_scene is not None:
                sb_img,sb_xy=sidebar_state(sb_scene,sb_pose); frame.paste(sb_img,sb_xy,sb_img); sidebar_seen.add(sb_scene)
            for name,side,active,state,expr in char_states:
                key=(name,expr,state,active)
                if key not in resized:
                    target_h=layout["character_active_height"] if active else layout["character_inactive_height"]
                    resized[key]=resize_character(variants[name][expr,state],target_h,1.0 if active else layout["inactive_opacity"])
                char_img=resized[key]
                if active and r and bf>0:
                    sc=1.0+0.05*math.sin(math.pi*bf)
                    char_img=char_img.resize((max(1,round(char_img.width*sc)),max(1,round(char_img.height*sc))),Image.Resampling.LANCZOS)
                x=layout["character_side_margin"] if side=="left" else W-layout["character_side_margin"]-char_img.width
                y=H-18-char_img.height-(round(10*bf) if (active and r) else 0)
                frame.paste(char_img,(x,y),char_img)
                character_positions[name]="LEFT" if x+char_img.width/2 < W/2 else "RIGHT"
                if i==0:
                    # 字幕に隠れにくい上半分を、実際に paste した位置から記録。
                    character_frame_evidence[name]={"rect":[x,y,x+char_img.width,y+max(1,char_img.height//2)]}

            draw_switch_overlay(frame,d,t,_sw,_band_font,W)
            if r and layout.get("caption_layout")=="compact_v18":
                draw_caption_compact(d,W,layout,args.font,speaker,caption,emph=[t for t in (r.get("caption_emphasis_terms") or []) if t])
            elif r:
                x1,y1,x2,y2=layout["zones"]["caption"]
                palette=layout["caption_colors"]; accent=palette[speaker]
                d.rounded_rectangle((x1,y1,x2,y2),radius=18,fill=layout.get("caption_backplates",{}).get(speaker, layout["caption_backplate"]),outline=layout.get("caption_board_outline",{}).get(speaker, accent),width=int(layout.get("caption_board_outline_width",4)))  # FIX v1.5: pale board per speaker
                d.rounded_rectangle((x1+18,y1+15,x1+150,y1+48),radius=13,fill=accent)
                d.text((x1+31,y1+21),speaker,font=small,fill="#17202F")
                max_lines=int(layout.get("caption_max_lines",3))
                _gap=int(layout.get("caption_line_gap",8))
                f,lines=fit_text(caption,args.font,layout["caption_font_size"],20,x2-x1-60,max_lines)
                available=max(1,y2-(y1+58)-12)
                while f.size>20 and len(lines)*(f.size+_gap)>available:
                    f,lines=fit_text(caption,args.font,f.size-2,20,x2-x1-60,max_lines)
                if f.size<24:  # WO 手順 7：24 px 未満は止める（RB-48）
                    raise ValueError("caption would shrink below 24 px (RB-48): %s" % caption[:20])
                if len(lines)*(f.size+_gap)>available:
                    raise ValueError("caption would clip outside approved lower-third safe area")
                yy=y1+58
                for line in lines:
                    tw=f.getlength(line)
                    d.text(((W-tw)/2,yy),line,font=f,fill=layout["caption_text_colors"][speaker],stroke_width=int(layout.get("caption_stroke_width",0)),stroke_fill=layout.get("caption_stroke_color","#FFFFFF"))  # FIX v1.4: white board, dark text
                    yy += f.size+_gap

            if layout.get("source_label_enabled"):
                _sl=_src_label.get(current_visual)
                if not _sl: raise ValueError("source label missing for visual %s" % current_visual)
                if label_font.getlength(_sl)>int(layout.get("source_label_max_px",700)): raise ValueError("source label too long for one line: %s" % _sl[:30])
                d.text((int(layout.get("source_label_right_x",1256)),int(layout.get("source_label_y",706))),_sl,font=label_font,fill=layout.get("source_label_color","#F2F6FA"),anchor="rm")  # 出典（RULES §3）
            if i==0:
                for evidence in character_frame_evidence.values():
                    evidence["rgb_8x8"]=list(frame.crop(tuple(evidence["rect"])).convert("RGB").resize((8,8),Image.Resampling.BOX).tobytes())
            raw=frame.tobytes()
            frame_cache[frame_key]=raw
            frame_cache.move_to_end(frame_key)
            while len(frame_cache)>cache_limit:
                frame_cache.popitem(last=False)
            process.stdin.write(raw)

    for name in ({r["speaker"] for r in records} if not getattr(args, "preview", False) else set()):
        if len(mouth_seen[name])<2: raise RuntimeError(f"{name}: no measured mouth change rendered")
        if os.environ.get("VIDEO_REVIEW_MODE") == "strict" and args.duration>=90 and len(expr_seen[name])<2: raise RuntimeError(f"{name}: longform missing authored expression changes")

    report={
      "preview_only":bool(getattr(args, "preview", False)),
      "duration":args.duration,"canvas":[W,H],"orientation":"LANDSCAPE_16_9",
      "mouth_states":{k:sorted(v) for k,v in mouth_seen.items()},
      "expressions":{k:sorted(v) for k,v in expr_seen.items()},
      "character_positions":character_positions,
      "character_frame_evidence":character_frame_evidence,
      "final_sha256":hashlib.sha256(out.read_bytes()).hexdigest(),
      "reference_grammar":"TECH_NEWS_INFORMATION_HIERARCHY_ONLY_NO_MORNING_SHOW_COPY",
      "voice_reused":True,"agenda_sidebar_scenes":(sorted(sidebar_seen) if sidebar_state else None),"one_final_encode":True,"visual_sha256":visual_hashes,"character_keys":char_keys,
      "audio_fast_path":{"gain_db":gain_db,"limiter_linear":limiter},
      "motion_diagrams":{"count":len(motion_timeline.get("diagrams",[])),"scope":"台詞同期の部品別図解。目視/聴感は別確認"},
      "visual_motion":{"layered":layered,"entry_seconds":perf_cfg.get("visual_entry_seconds"),"nudge_every_seconds":perf_cfg.get("visual_beat_seconds"),"nudge_seconds":perf_cfg.get("visual_nudge_seconds"),"nudge_scale":perf_cfg.get("visual_nudge_scale")},
      "chapter_cards":{"count":len(chapter_cards),"frames_drawn":cards_drawn,"windows":[[c["start"],c["end"]] for c in chapter_cards]},
      "mouth_update_hz":mouth_hz,
      "moviepy_video_visuals":len(video_panels.paths),
      "frame_state_cache":{"limit":cache_limit,"hits":cache_hits,"misses":cache_misses},
      "render_wall_seconds":round(time.monotonic()-render_started,3),
      "render_realtime_factor":round(args.duration/max(0.001,time.monotonic()-render_started),3)
    }
    out.with_suffix(".report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__=="__main__":
    p=argparse.ArgumentParser()
    for name in ("audio","timing","shell","font","visual","output"): p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--profile",type=Path,default=Path(__file__).resolve().parents[1]/"config/approved_landscape_video_template.json")
    p.add_argument("--presentation",type=Path,required=True)
    p.add_argument("--cache-root",type=Path)
    p.add_argument("--start",type=float,required=True)
    p.add_argument("--duration",type=float,required=True)
    p.add_argument("--video-only",action="store_true")
    p.add_argument("--preview",action="store_true",help="1〜2秒の試し描画。全編口パクguardの合格記録には使わない")
    signal.signal(signal.SIGTERM, interrupt_encoder)
    render(p.parse_args())

