"""描画器と共用する表情・実フォント・字幕配置の事前検査。"""
import math
import os
import re
from PIL import Image, ImageDraw, ImageFont


def render_frame_count(samples, rate, fps, start, duration):
    if start == 0 and math.isclose(duration, samples / rate, rel_tol=0, abs_tol=1e-9):
        return (samples * fps + rate - 1) // rate
    return math.ceil(duration * fps)


def visible_interval(row, cards, fps):
    """実際のフレーム格子上で、カードに隠れない台詞が1枚でもあるか。"""
    first = math.ceil(row["start"] * fps)
    last = math.ceil(row["end"] * fps)
    for frame in range(first, last):
        t = frame / fps
        if not any(c["start"] <= t < c["end"] for c in cards):
            return True
    return False


def validate_render_plan(profile, presentation, timing, font, draw_caption=None, caption_states=None):
    layout = profile["layout"]
    records = timing["records"] if isinstance(timing, dict) else timing
    cards = presentation.get("chapter_cards", [])
    fps = layout["fps"]
    errors = []
    warnings = []
    batch = os.environ.get('VIDEO_REVIEW_MODE','strict') == 'batch'
    expressions = {}
    for row in records:
        speaker, emotion = row.get("speaker"), row.get("emotion")
        if emotion not in profile["acting"]["expressions"]:
            errors.append(f"台詞{row.get('id')}: 未対応または未指定の表情")
        if visible_interval(row, cards, fps):
            expressions.setdefault(speaker, set()).add(emotion)
    # 短い試し描画でも、全編の計画に対して確認する。
    if records and max(r["end"] for r in records) >= 90:
        for speaker in {r.get("speaker") for r in records}:
            if len(expressions.get(speaker, set())) < 2:
                (warnings if batch else errors).append(f"{speaker}: カード外の明示的な表情が2種類未満")
    label_font = ImageFont.truetype(str(font), int(layout.get("source_label_font_size", 12)))
    label_widths = {}
    if layout.get("source_label_enabled"):
        for visual in presentation.get("visuals", []):
            label = visual.get("source_label")
            if not isinstance(label, str) or not label.strip():
                errors.append(f"画像{visual.get('id')}: 出典ラベルがない")
                continue
            width = label_font.getlength(label)
            label_widths[visual["id"]] = width
            if width > int(layout.get("source_label_max_px", 700)):
                errors.append(f"画像{visual['id']}: 出典ラベル幅超過({width:.1f}px)")
    caption_checks = []
    if draw_caption is not None:
        canvas = Image.new("RGB", (layout["width"], layout["height"]))
        real_draw = ImageDraw.Draw(canvas)
        width = int(layout.get("caption_box_width", 760))
        left, right = (layout["width"] - width) // 2, (layout["width"] + width) // 2
        _, top, _, bottom = layout["zones"]["caption"]
        board_top = top + int(layout.get("caption_tab_height", 36)) + int(layout.get("caption_tab_gap", 6))

        class Probe:
            def __getattr__(self, key):
                return getattr(real_draw, key)

            def text(self, xy, text, **kwargs):
                # 名前タブは字幕本文の36px条件の対象外。
                if kwargs.get("anchor") != "mm":
                    font = kwargs["font"]
                    bbox = real_draw.textbbox(xy, text, font=font, anchor=kwargs.get("anchor"))
                    if font.size < 36:
                        raise ValueError(f"字幕が36px未満: {font.size}px")
                    if not (left <= bbox[0] <= bbox[2] <= right and board_top <= bbox[1] <= bbox[3] <= bottom):
                        raise ValueError("字幕文字が板の領域をはみ出す")
                return real_draw.text(xy, text, **kwargs)

        for row in records:
            # 描画器と同じ文分割・描画関数を、全字幕状態に適用する。
            if not visible_interval(row, cards, fps):
                continue
            try:
                clauses = (caption_states(row.get("caption_text", ""), font, layout, row.get("caption_emphasis_terms") or ())
                           if caption_states else [x for x in re.split(r"(?<=[。！？])", row.get("caption_text", "")) if x])
            except ValueError as e:
                errors.append(f"台詞{row.get('id')}: {e}")
                continue
            for clause in clauses:
                try:
                    draw_caption(Probe(), layout["width"], layout, font, row["speaker"], clause,
                                 emph=row.get("caption_emphasis_terms") or ())
                    caption_checks.append(row["id"])
                except (OSError, ValueError, KeyError) as e:
                    errors.append(f"台詞{row.get('id')}: {e}")
    if errors:
        raise ValueError("描画前検査FAIL:\n" + "\n".join(errors))
    return {"status": "PASS", "warnings": warnings, "expressions": {k: sorted(v) for k, v in expressions.items()},
            "label_widths": label_widths, "caption_states_checked": len(caption_checks),
            "caption_scope": "全字幕状態の静的配置。動画画素・動的重なり・聴感は別検査"}
