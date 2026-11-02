"""Dynamic visuals engine: non-overlapping side layout for topic and minimap,
step-by-step diagram highlights with bounding boxes, robust coordinate validation,
timeline continuity across segment rendering, and visual replacement safety.

Conforms to VIDEO_PRODUCTION_STANDARD.md §5.1, VIDEO_KNOWHOW.md, and RULES.md.
"""
from __future__ import annotations
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image, ImageDraw, ImageFont, ImageOps

DEFAULT_TRANSITION_SECONDS = 0.8  # Target: 0.6 to 1.2s
MIN_TRANSITION_SECONDS = 0.6
MAX_TRANSITION_SECONDS = 1.2
DEFAULT_HOLD_SECONDS = 3.0       # Target: 2.0 to 4.0s
MIN_BOX_AREA = 0.001             # Prevent zero / collapsed bounding boxes

VALID_MOTIONS = {"overview", "zoom_in", "pan", "zoom_out", "static", "diagram_step"}
FULL_BOX = (0.0, 0.0, 1.0, 1.0)


def clamp(val: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    return max(min_val, min(max_val, val))


def ease_in_out_cubic(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    if x < 0.5:
        return 4.0 * x * x * x
    else:
        return 1.0 - math.pow(-2.0 * x + 2.0, 3) / 2.0


def validate_box(raw_box: Any) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[str]]:
    """Validate bounding box [ymin, xmin, ymax, xmax] with ratio coordinates (0.0 to 1.0).

    Safely handles dict {x, y, w, h} or {ymin, xmin, ymax, xmax} without throwing TypeError
    even if values are strings, None, bool, NaN, or Inf.
    Returns (normalized_tuple, fallback_reason).
    """
    if raw_box is None:
        return None, "box_none"

    raw_coords = []
    if isinstance(raw_box, dict):
        if all(k in raw_box for k in ('ymin', 'xmin', 'ymax', 'xmax')):
            raw_coords = [raw_box['ymin'], raw_box['xmin'], raw_box['ymax'], raw_box['xmax']]
        elif all(k in raw_box for k in ('y', 'x', 'w', 'h')):
            # Crucial: validate types BEFORE attempting addition y + h or x + w
            vals = [raw_box['y'], raw_box['x'], raw_box['h'], raw_box['w']]
            for i, v in enumerate(vals):
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    return None, f"dict_value_{i}_not_a_number"
                if math.isnan(v) or math.isinf(v):
                    return None, f"dict_value_{i}_is_nan_or_inf"
            y, x, h, w = float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
            raw_coords = [y, x, y + h, x + w]
        else:
            return None, "invalid_box_dict_keys"
    elif isinstance(raw_box, (list, tuple)):
        raw_coords = list(raw_box)
    else:
        return None, "invalid_box_type"

    if len(raw_coords) != 4:
        return None, f"invalid_box_length_{len(raw_coords)}"

    # Check that each element is a finite float/int and not bool
    for i, c in enumerate(raw_coords):
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            return None, f"coordinate_{i}_not_a_number"
        if math.isnan(c) or math.isinf(c):
            return None, f"coordinate_{i}_is_nan_or_inf"

    ymin, xmin, ymax, xmax = round(float(raw_coords[0]), 6), round(float(raw_coords[1]), 6), round(float(raw_coords[2]), 6), round(float(raw_coords[3]), 6)

    # Range check
    if not (0.0 <= ymin <= 1.0 and 0.0 <= xmin <= 1.0 and 0.0 <= ymax <= 1.0 and 0.0 <= xmax <= 1.0):
        return None, "coordinates_out_of_0_1_range"

    # Inversion check
    if ymin >= ymax:
        return None, "inverted_vertical_coordinates"
    if xmin >= xmax:
        return None, "inverted_horizontal_coordinates"

    # Area check
    area = (ymax - ymin) * (xmax - xmin)
    if area < MIN_BOX_AREA:
        return None, "box_area_too_small"

    return (ymin, xmin, ymax, xmax), None


def parse_diagram_steps(raw_steps: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse sequential diagram steps with optional ratio bounding boxes for elements/arrows/rows/series."""
    if not raw_steps or not isinstance(raw_steps, list):
        return [], None

    parsed, issues = [], []
    for idx, item in enumerate(raw_steps):
        if isinstance(item, str):
            label = item.strip()
            if label:
                parsed.append({"label": label, "box": None})
        elif isinstance(item, dict):
            label = str(item.get("label") or f"Step {idx + 1}").strip()
            raw_box = item.get("box") or item.get("focus_area") or item.get("rect")
            step_box, error = validate_box(raw_box)
            if raw_box is not None and error:
                issues.append(f"step_{idx + 1}_{error}")
            parsed.append({"label": label, "box": step_box})
    return parsed, ';'.join(issues) or None


def parse_visual_cue(data: Dict[str, Any], prev_cue: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse and normalize visual cue from line, record or project specification.

    Invalid specifications fall back to 'static' full view with reason recorded.
    """
    topic = data.get("topic")
    if topic is not None:
        topic = str(topic).strip()
        if not topic:
            topic = None

    raw_motion = data.get("motion") or data.get("motion_type")
    motion = str(raw_motion).strip().lower() if raw_motion else "static"
    fallback_reason = None
    if motion not in VALID_MOTIONS:
        fallback_reason = f"unknown_motion_{raw_motion}"
        motion = "static"

    raw_box = data.get("focus_area") or data.get("focus_rect") or data.get("target_area")
    box, box_err = validate_box(raw_box)
    if box_err and raw_box is not None:
        fallback_reason = box_err

    # Diagram steps
    raw_steps = data.get("diagram_steps") or data.get("diagram_highlights")
    diagram_steps, step_error = parse_diagram_steps(raw_steps)

    # Fallback logic: if motion implies focus area (zoom_in / pan) but box is invalid,
    # safely fall back to static full-frame and record reason.
    if box is None:
        if raw_box is None and not fallback_reason and motion in ("zoom_in", "pan"):
            fallback_reason = "no_focus_area_specified"
        box = FULL_BOX
    if fallback_reason:
        box = FULL_BOX
        motion = "static"
        diagram_steps = []

    # Determine previous box reference
    prev_box = FULL_BOX
    if prev_cue and prev_cue.get("box"):
        prev_box = prev_cue["box"]

    show_minimap = data.get("show_minimap", True)

    is_custom = (topic is not None) or (box != FULL_BOX) or bool(diagram_steps) or (motion != "static")

    return {
        "visual_id": data.get("visual_id") or data.get("visual"),
        "topic": topic,
        "motion": motion,
        "box": box,
        "prev_box": prev_box,
        "diagram_steps": diagram_steps,
        "show_minimap": bool(show_minimap and box != FULL_BOX),
        "fallback_reason": fallback_reason,
        "step_fallback_reason": step_error,
        "is_custom": is_custom,
    }


def build_cue_timeline(all_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build complete cue timeline across all records from whole video timing.

    Ensures continuity across segments (args.start):
    - When visual_id changes or an unguided line intervenes, previous box does NOT carry over.
    - When panning after zoom_out, starts from FULL_BOX (the final state of zoom_out).
    """
    timeline = []
    prev_valid_cue: Optional[Dict[str, Any]] = None
    prev_visual_id: Optional[str] = None
    prev_record = None

    for rec in all_records:
        rec_visual = rec.get("visual_id") or rec.get("visual")
        # If visual changed, do not inherit previous box
        if prev_visual_id is not None and rec_visual != prev_visual_id:
            prev_valid_cue = None

        cue = parse_visual_cue(rec, prev_valid_cue)

        if prev_valid_cue:
            if prev_record and 'start' in prev_record and 'end' in prev_record:
                duration = max(0.0, prev_record['end'] - prev_record['start'])
                cue['prev_box'] = compute_crop_window(prev_valid_cue, duration, duration)[0]
            elif prev_valid_cue.get("motion") == "zoom_out":
                cue['prev_box'] = FULL_BOX

        timeline.append(cue)

        if cue.get("is_custom"):
            prev_valid_cue = cue
        else:
            # Unguided line intervenes -> reset previous box inheritance
            prev_valid_cue = None

        prev_visual_id = rec_visual
        prev_record = rec

    return timeline


def held_cue_before(records, cues, start):
    """Restore the last completed cue when a segment starts inside a silent gap."""
    previous = [(record, cue) for record, cue in zip(records, cues)
                if record['end'] <= start]
    if not previous:
        return None, 1.0
    record, cue = max(previous, key=lambda item: item[0]['end'])
    return cue, max(0.0, record['end'] - record['start'])


def compute_crop_window(
    cue: Dict[str, Any],
    elapsed: float,
    line_duration: float
) -> Tuple[Tuple[float, float, float, float], float, float]:
    """Calculate the current camera crop window [ymin, xmin, ymax, xmax] in ratio coordinates.

    Returns (crop_window, motion_progress, hold_progress).
    """
    motion = cue.get("motion", "static")
    target_box = cue.get("box", FULL_BOX)
    prev_box = cue.get("prev_box", FULL_BOX)

    max_allowed = max(0.3, line_duration * 0.45) if line_duration > 0 else DEFAULT_TRANSITION_SECONDS
    trans_dur = min(DEFAULT_TRANSITION_SECONDS, max_allowed)
    trans_dur = max(0.2, trans_dur)

    if elapsed < 0:
        elapsed = 0.0

    p = min(1.0, elapsed / trans_dur) if trans_dur > 0 else 1.0
    eased = ease_in_out_cubic(p)

    if motion == "static":
        return target_box, 1.0, 1.0

    elif motion == "overview":
        overview_pause = min(0.6, line_duration * 0.25)
        if elapsed < overview_pause:
            return FULL_BOX, 0.0, 0.0
        p_sub = min(1.0, (elapsed - overview_pause) / trans_dur)
        e_sub = ease_in_out_cubic(p_sub)
        return interpolate_box(FULL_BOX, target_box, e_sub), p_sub, 1.0 if p_sub >= 1.0 else 0.0

    elif motion == "zoom_in":
        return interpolate_box(FULL_BOX, target_box, eased), p, 1.0 if p >= 1.0 else 0.0

    elif motion == "pan":
        return interpolate_box(prev_box, target_box, eased), p, 1.0 if p >= 1.0 else 0.0

    elif motion == "zoom_out":
        start_box = prev_box if prev_box != FULL_BOX else target_box
        return interpolate_box(start_box, FULL_BOX, eased), p, 1.0 if p >= 1.0 else 0.0

    elif motion == "diagram_step":
        return target_box, 1.0, 1.0

    return target_box, 1.0, 1.0


def interpolate_box(
    b1: Tuple[float, float, float, float],
    b2: Tuple[float, float, float, float],
    t: float
) -> Tuple[float, float, float, float]:
    y1 = b1[0] + (b2[0] - b1[0]) * t
    x1 = b1[1] + (b2[1] - b1[1]) * t
    y2 = b1[2] + (b2[2] - b1[2]) * t
    x2 = b1[3] + (b2[3] - b1[3]) * t
    return (clamp(y1), clamp(x1), clamp(y2), clamp(x2))


def get_dynamic_cache_key(
    cue: Dict[str, Any],
    elapsed: float,
    line_duration: float,
    fps: int = 24
) -> Tuple[Any, ...]:
    """Cache key including step highlights, topic, motion, and holding state."""
    if not cue.get("is_custom"):
        return ("no_cue",)

    crop_window, p, _ = compute_crop_window(cue, elapsed, line_duration)
    if p < 1.0:
        frame_idx = round(elapsed * fps)
        motion_state = ("moving", frame_idx)
    else:
        motion_state = ("holding",)

    steps = cue.get("diagram_steps", [])
    active_step = -1
    active_step_label = None
    active_step_box = None
    if steps and line_duration > 0:
        step_idx = int((elapsed / line_duration) * len(steps))
        active_step = min(len(steps) - 1, step_idx)
        active_step_label = steps[active_step].get("label")
        active_step_box = steps[active_step].get("box")

    return (
        cue.get("visual_id"),
        cue.get("topic"),
        cue.get("motion"),
        tuple(round(v, 3) for v in crop_window),
        motion_state,
        active_step,
        active_step_label,
        active_step_box,
        cue.get("show_minimap"),
        cue.get("fallback_reason"),
    )


def resolve_font(font_path: Any, size: int) -> ImageFont.ImageFont:
    """Robust font loader supporting font file paths or fallback to default."""
    try:
        if font_path and Path(str(font_path)).is_file():
            return ImageFont.truetype(str(font_path), size)
    except Exception:
        pass
    # Fallback attempts
    for candidate in ("ZenMaruGothic-Medium.ttf", "msgothic.ttc", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def wrap_text_lines(text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """Wrap Japanese text without truncating."""
    if not text:
        return []
    lines = []
    current = ""
    for ch in text:
        test_line = current + ch
        try:
            w = font.getlength(test_line)
        except Exception:
            w = len(test_line) * 16
        if w > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test_line
    if current:
        lines.append(current)
    return lines


def draw_dynamic_source_label(frame, label, font_path, evidence_zone):
    """A fixed footer, 13px at 360p, independent of the screenshot camera."""
    left, _, right, _ = evidence_zone
    font = resolve_font(font_path, 26)
    visible = str(label)
    while visible and font.getlength(visible) > right-left-16:
        visible = visible[:-2] + '…'
    draw = ImageDraw.Draw(frame)
    text_box = draw.textbbox((right-8, frame.height-4), visible, font=font, anchor='rb')
    draw.rounded_rectangle((text_box[0]-7, text_box[1]-3, text_box[2]+7, text_box[3]+3),
                           radius=4, fill='#17342A')
    draw.text((right-8, frame.height-4), visible, font=font, fill='#FFFFFF', anchor='rb')
    return text_box


def render_dynamic_visual_panel(
    raw_src_image: Image.Image,
    evidence_width: int,
    evidence_height: int,
    cue: Dict[str, Any],
    elapsed: float,
    line_duration: float,
    font_path: Path | str,
    panel_bg_color: str = "#FAF8F3",
    border_color: str = "#C9D5E3",
) -> Image.Image:
    """Render the full evidence panel (e.g. 830x258) using a dedicated side layout.

    Topic name, minimap, and step indicators are placed in a dedicated right-side margin,
    NEVER obscuring the reference image content or text.
    """
    panel = Image.new("RGBA", (evidence_width, evidence_height), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(panel)

    # 1. Evidence panel background
    pdraw.rounded_rectangle(
        (0, 0, evidence_width - 1, evidence_height - 1),
        radius=18,
        fill=panel_bg_color,
        outline=border_color,
        width=2,
    )

    pad = 10
    total_w = evidence_width - 2 * pad
    total_h = evidence_height - 2 * pad

    topic = cue.get("topic")
    crop_window, motion_p, _ = compute_crop_window(cue, elapsed, line_duration)
    ymin, xmin, ymax, xmax = crop_window
    is_zoomed = (xmax - xmin) < 0.95 or (ymax - ymin) < 0.95
    steps = cue.get("diagram_steps", [])
    has_side_info = bool(topic or (cue.get("show_minimap") and is_zoomed) or steps)

    # 2. Partition into main visual area and side info area
    if has_side_info:
        side_w = 220
        gap = 12
        main_w = total_w - side_w - gap
        main_h = total_h
        side_x = pad + main_w + gap
        side_y = pad
    else:
        main_w = total_w
        main_h = total_h
        side_w = 0

    # 3. Render cropped reference image in the main visual area
    src_w, src_h = raw_src_image.size
    crop_px = (
        int(round(xmin * src_w)),
        int(round(ymin * src_h)),
        max(int(round(xmin * src_w)) + 1, int(round(xmax * src_w))),
        max(int(round(ymin * src_h)) + 1, int(round(ymax * src_h))),
    )
    cropped = raw_src_image.crop(crop_px)
    scaled_content = ImageOps.contain(cropped, (main_w, main_h), Image.Resampling.LANCZOS)
    c_x = pad + (main_w - scaled_content.width) // 2
    c_y = pad + (main_h - scaled_content.height) // 2
    panel.paste(scaled_content, (c_x, c_y))

    # Inner border around image
    pdraw.rectangle((c_x - 1, c_y - 1, c_x + scaled_content.width, c_y + scaled_content.height), outline="#D1D5DB", width=1)

    # 4. Highlight target box and active diagram step box directly on the image
    target_box = cue.get("box", FULL_BOX)
    scale_x = scaled_content.width / max(1e-6, (xmax - xmin))
    scale_y = scaled_content.height / max(1e-6, (ymax - ymin))

    # Determine active diagram step
    active_step_idx = -1
    if steps and line_duration > 0:
        step_idx = int((elapsed / line_duration) * len(steps))
        active_step_idx = min(len(steps) - 1, step_idx)

    # If active step has its own box, highlight it; otherwise highlight target_box
    active_box = None
    if 0 <= active_step_idx < len(steps):
        active_box = steps[active_step_idx].get("box")

    highlight_box = active_box if active_box else (target_box if target_box != FULL_BOX else None)

    if highlight_box and motion_p > 0.4:
        t_ymin, t_xmin, t_ymax, t_xmax = highlight_box
        rx1 = c_x + int(round((t_xmin - xmin) * scale_x))
        ry1 = c_y + int(round((t_ymin - ymin) * scale_y))
        rx2 = c_x + int(round((t_xmax - xmin) * scale_x))
        ry2 = c_y + int(round((t_ymax - ymin) * scale_y))

        rx1 = max(c_x, min(c_x + scaled_content.width, rx1))
        ry1 = max(c_y, min(c_y + scaled_content.height, ry1))
        rx2 = max(c_x, min(c_x + scaled_content.width, rx2))
        ry2 = max(c_y, min(c_y + scaled_content.height, ry2))

        if (rx2 - rx1) > 4 and (ry2 - ry1) > 4:
            alpha = int(round(clamp((motion_p - 0.4) / 0.6) * 220))
            overlay = Image.new("RGBA", panel.size, (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            # Outline accent
            odraw.rounded_rectangle(
                (rx1 - 2, ry1 - 2, rx2 + 2, ry2 + 2),
                radius=4,
                outline=(230, 75, 45, alpha),
                width=3,
            )
            # Subtle warm highlight
            odraw.rectangle((rx1, ry1, rx2, ry2), fill=(255, 235, 120, int(alpha * 0.20)))
            panel = Image.alpha_composite(panel, overlay)
            pdraw = ImageDraw.Draw(panel)

    # 5. Render Dedicated Side Info Panel (NO OVERLAP with main visual)
    if has_side_info:
        # Background card for side panel
        pdraw.rounded_rectangle(
            (side_x, side_y, side_x + side_w, side_y + total_h),
            radius=12,
            fill="#EDF2F7",
            outline="#CBD5E1",
            width=2,
        )

        curr_y = side_y + 12
        side_inner_w = side_w - 20
        side_inner_x = side_x + 10

        # Topic header
        if topic:
            # Topic font: start at 32px for 640x360 readability, down to 24px if multi-line
            f_topic = resolve_font(font_path, 30)
            wrapped = wrap_text_lines(topic, f_topic, side_inner_w)
            if len(wrapped) > 2:
                f_topic = resolve_font(font_path, 24)
                wrapped = wrap_text_lines(topic, f_topic, side_inner_w)

            # Topic tag badge
            f_tag = resolve_font(font_path, 14)
            pdraw.text((side_inner_x, curr_y), "■ トピック", font=f_tag, fill="#4A5568")
            curr_y += 18

            shown_topic = wrapped[:3]
            if len(wrapped) > 3:
                shown_topic[-1] = shown_topic[-1][:-1] + '…'
            for line in shown_topic:
                pdraw.text((side_inner_x, curr_y), line, font=f_topic, fill="#1A202C")
                curr_y += int(getattr(f_topic, "size", 28) * 1.15)
            curr_y += 8

        # Diagram steps list (in side panel, NOT over the image)
        if steps:
            pdraw.line((side_inner_x, curr_y, side_inner_x + side_inner_w, curr_y), fill="#CBD5E1", width=1)
            curr_y += 6
            f_step_title = resolve_font(font_path, 14)
            pdraw.text((side_inner_x, curr_y), "■ 説明手順", font=f_step_title, fill="#4A5568")
            curr_y += 18
            f_step = resolve_font(font_path, 18)

            active_step = steps[max(0, active_step_idx)]
            step_text = f'{max(0, active_step_idx)+1}/{len(steps)} ' + active_step['label']
            f_step = resolve_font(font_path, 24)
            available_lines = max(1, int((side_y+total_h-curr_y-8)//28))
            step_lines = wrap_text_lines(step_text, f_step, side_inner_w)
            shown_steps = step_lines[:available_lines]
            if len(step_lines) > available_lines:
                shown_steps[-1] = shown_steps[-1][:-1] + '…'
            for line in shown_steps:
                pdraw.text((side_inner_x, curr_y), line, font=f_step, fill='#2B6146')
                curr_y += 28
            curr_y += 6

        # Minimap (in side panel, NOT over the image)
        if cue.get("show_minimap") and is_zoomed:
            rem_h = (side_y + total_h) - curr_y - 20
            if rem_h >= 60:
                pdraw.line((side_inner_x, curr_y, side_inner_x + side_inner_w, curr_y), fill="#CBD5E1", width=1)
                curr_y += 4
                f_mini_lbl = resolve_font(font_path, 14)
                pdraw.text((side_inner_x, curr_y), "■ 資料内位置", font=f_mini_lbl, fill="#4A5568")
                curr_y += 18

                mini_avail_w = side_inner_w
                mini_avail_h = max(40, (side_y + total_h) - curr_y - 8)
                mini_img = ImageOps.contain(raw_src_image, (mini_avail_w, mini_avail_h), Image.Resampling.BILINEAR)

                mx = side_inner_x + (mini_avail_w - mini_img.width) // 2
                my = curr_y + (mini_avail_h - mini_img.height) // 2
                panel.paste(mini_img, (mx, my))

                # Viewport box on minimap
                mw, mh = mini_img.width, mini_img.height
                vx1 = mx + int(round(xmin * mw))
                vy1 = my + int(round(ymin * mh))
                vx2 = mx + int(round(xmax * mw))
                vy2 = my + int(round(ymax * mh))
                pdraw.rectangle((mx - 1, my - 1, mx + mw, my + mh), outline="#A0AEC0", width=1)
                pdraw.rectangle((vx1, vy1, vx2, vy2), outline="#E53E3E", width=2)

    return panel
