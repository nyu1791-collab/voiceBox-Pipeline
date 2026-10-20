"""中央の説明パネルの表示契約（指示書 2026-10-19）。

- 中央パネルにナレーションの文章を再掲しない。中央の帯は要約（1〜2行）だけを出す。
- 要約は1行30字以内、2行まで。ナレーションと重複させない。
- 顔出し（R45 ひょっこり）は廃止。章カードにも人物を出さない（CHAPTER_CARD_CHARACTERS）。
- 画像・表・グラフの枠に文字（図解の行）を載せない（R69、MOTION_TEXT_IN_VISUAL_SLOT）。
"""
import re

PEEK_ENABLED = False  # R45 廃止（2026-10-19）。戻す場合は True にし、作業記録に理由を書く
MOTION_TEXT_IN_VISUAL_SLOT = False  # R69（2026-10-19）：図解の文字は、画像・表・グラフの枠に載せない。枠の中は画像だけ
CHAPTER_CARD_CHARACTERS = False  # 2026-10-19：章カードに人物を出さない（覗き・顔出しの廃止の延長）
SUMMARY_MAX_LINES = 2
SUMMARY_MAX_CHARS = 30
SUMMARY_COPY_RATIO = 0.8  # ナレーションの8割以上の丸写しを拒否。重要語・数値の再掲は許可（指示書 §2）
SUMMARY_LINE_H = 30  # 要約の行の高さ（px）。中央パneルの下、字幕の板の上に収める

_STRIP = re.compile(r"[\s、。,.!！?？「」『』・\-—()（）\[\]［］【】：:；;／/\"'“”]")


def _norm(text):
    return _STRIP.sub("", str(text))


def summary_lines(item):
    """提示の visual から、要約の行を取り出す（空の行は除く）。"""
    return [str(x).strip() for x in (item.get("summary") or []) if str(x).strip()]


def is_copy(a, b):
    """a と b のどちらかが、もう一方の8割以上を含む時だけ丸写しとみなす（短い重要語は含まない）。"""
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return short in long_ and len(short) >= SUMMARY_COPY_RATIO * len(long_)


def check_summary(summary, narrations):
    """要約の検査。違反の説明の一覧を返す（空なら合格）。

    summary：要約の行（list[str]）。narrations：ナレーション（字幕の元の文）の一覧。
    """
    errors = []
    lines = [str(x).strip() for x in (summary or []) if str(x).strip()]
    if len(lines) > SUMMARY_MAX_LINES:
        errors.append("要約は%d行まで（%d行）" % (SUMMARY_MAX_LINES, len(lines)))
    for line in lines:
        if len(line) > SUMMARY_MAX_CHARS:
            errors.append("要約1行は%d字以内（%d字）: %s" % (SUMMARY_MAX_CHARS, len(line), line[:12]))
    spoken = [_norm(t) for t in narrations if _norm(t)]
    for line in lines:
        n = _norm(line)
        if any(is_copy(n, t) for t in spoken):
            errors.append("要約がナレーションの丸写し: %s" % line[:20])
    return errors


def summary_box(zones_evidence, n_lines):
    """要約の枠（x1, y1, x2, y2）。説明パネルの直下、字幕の板の上（498px）より上に収める。"""
    ex1, ey1, ex2, ey2 = zones_evidence
    y1 = ey2 + 4
    y2 = y1 + n_lines * SUMMARY_LINE_H + 4
    return ex1, y1, ex2, y2
