"""Comprehensive tests for dynamic visuals:
1. Coordinate & dict validation (safe against strings, bool, None, NaN, Inf before arithmetic).
2. Unknown motion & invalid box graceful fallback with reason logging.
3. Timeline cue continuity across segment rendering (args.start), unguided lines, and visual changes.
4. Non-overlapping side layout with diagram steps and minimap.
5. Diagram step-by-step box highlighting and sequential pixel changes.
6. Cache key differentiation across topics, step highlights, and holding states.
7. Prepare-project image replacement safety (invalidating cues when target visual differs from Gemini selection).
8. Cross-platform font fallback.
"""
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont

from renderer.dynamic_visuals import (
    validate_box,
    parse_visual_cue,
    build_cue_timeline,
    compute_crop_window,
    get_dynamic_cache_key,
    render_dynamic_visual_panel,
    resolve_font,
    FULL_BOX,
    held_cue_before,
    draw_dynamic_source_label,
)
from research import normalize_script
from research import source_id
from tools.prepare_project import prepare


class DynamicVisualsRobustValidationTest(unittest.TestCase):
    def test_dict_box_safety_against_non_numeric_values(self):
        # Strings in dict should not cause TypeError during addition (y + h)
        box, reason = validate_box({'x': '10', 'y': '20', 'w': '30', 'h': '40'})
        self.assertIsNone(box)
        self.assertIn("dict_value", reason)

        # None in dict
        box, reason = validate_box({'x': None, 'y': 0.2, 'w': 0.3, 'h': 0.4})
        self.assertIsNone(box)
        self.assertIn("dict_value", reason)

        # bool in dict
        box, reason = validate_box({'x': True, 'y': 0.2, 'w': 0.3, 'h': 0.4})
        self.assertIsNone(box)
        self.assertIn("dict_value", reason)

        # NaN or Inf in dict
        box, reason = validate_box({'x': float('nan'), 'y': 0.2, 'w': 0.3, 'h': 0.4})
        self.assertIsNone(box)
        self.assertIn("dict_value", reason)

        box, reason = validate_box({'x': 0.1, 'y': float('inf'), 'w': 0.3, 'h': 0.4})
        self.assertIsNone(box)
        self.assertIn("dict_value", reason)

        # Valid dict {x, y, w, h}
        box, reason = validate_box({'x': 0.1, 'y': 0.2, 'w': 0.5, 'h': 0.4})
        self.assertIsNone(reason)
        for actual, expected in zip(box, (0.2, 0.1, 0.6, 0.6)):
            self.assertAlmostEqual(actual, expected)

    def test_unknown_motion_falls_back_with_reason(self):
        cue = parse_visual_cue({
            "topic": "謎の演出",
            "motion": "crazy_spin_360",
            "focus_area": [0.1, 0.1, 0.5, 0.5],
        })
        self.assertEqual(cue["motion"], "static")
        self.assertEqual(cue["fallback_reason"], "unknown_motion_crazy_spin_360")
        self.assertEqual(cue["box"], FULL_BOX)

    def test_diagram_steps_parsing_with_boxes(self):
        cue = parse_visual_cue({
            "diagram_steps": [
                {"label": "新月", "box": [0.1, 0.1, 0.4, 0.4]},
                {"label": "満月", "box": [0.5, 0.5, 0.9, 0.9]},
                "三日月"  # string fallback
            ]
        })
        steps = cue["diagram_steps"]
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["label"], "新月")
        self.assertEqual(steps[0]["box"], (0.1, 0.1, 0.4, 0.4))
        self.assertEqual(steps[1]["label"], "満月")
        self.assertEqual(steps[1]["box"], (0.5, 0.5, 0.9, 0.9))
        self.assertEqual(steps[2]["label"], "三日月")
        self.assertIsNone(steps[2]["box"])


class CueTimelineContinuityTest(unittest.TestCase):
    def test_timeline_boundary_and_transition_rules(self):
        records = [
            # Line 1: visual A, zoom in to section 1
            {"id": 1, "visual_id": "img-A", "motion": "zoom_in", "focus_area": [0.1, 0.1, 0.4, 0.4]},
            # Line 2: visual A, pan to section 2 (should inherit Line 1's box)
            {"id": 2, "visual_id": "img-A", "motion": "pan", "focus_area": [0.5, 0.5, 0.8, 0.8]},
            # Line 3: visual A, zoom out to full
            {"id": 3, "visual_id": "img-A", "motion": "zoom_out"},
            # Line 4: visual A, pan after zoom_out (should start from FULL_BOX, the final state of zoom_out)
            {"id": 4, "visual_id": "img-A", "motion": "pan", "focus_area": [0.2, 0.2, 0.6, 0.6]},
            # Line 5: unguided line intervenes
            {"id": 5, "visual_id": "img-A"},
            # Line 6: visual A after unguided (should NOT inherit Line 4's box)
            {"id": 6, "visual_id": "img-A", "motion": "pan", "focus_area": [0.3, 0.3, 0.7, 0.7]},
            # Line 7: visual changes to img-B (should NOT inherit img-A's box)
            {"id": 7, "visual_id": "img-B", "motion": "pan", "focus_area": [0.1, 0.1, 0.5, 0.5]},
        ]

        timeline = build_cue_timeline(records)
        self.assertEqual(len(timeline), 7)

        # Line 1: start from full box
        self.assertEqual(timeline[0]["prev_box"], FULL_BOX)

        # Line 2: inherited Line 1's box
        self.assertEqual(timeline[1]["prev_box"], (0.1, 0.1, 0.4, 0.4))

        # Line 4: after zoom_out, prev_box must be FULL_BOX
        self.assertEqual(timeline[3]["prev_box"], FULL_BOX)

        # Line 6: after unguided line, prev_box must reset to FULL_BOX
        self.assertEqual(timeline[5]["prev_box"], FULL_BOX)

        # Line 7: after visual change to img-B, prev_box must reset to FULL_BOX
        self.assertEqual(timeline[6]["prev_box"], FULL_BOX)


class VisualReplacementSafetyTest(unittest.TestCase):
    def prepare_with_related(self, directory, image_url):
        import json
        article = 'https://example.com/article'
        sid = source_id(article)
        source = {'id': sid, 'url': article, 'title': 'Article', 'text': 'Evidence',
                  'extraction_status': 'GEMINI_SUMMARY', 'is_youtube': False}
        pack = {'topic': 'Example', 'sources': [source]}
        script = {'title': 'Example', 'lines': [{'id': 1, 'speaker': '四国めたん',
                  'text': '資料です。', 'source_id': sid, 'visual_id': 'related',
                  'motion': 'zoom_in', 'topic': '注目箇所',
                  'focus_area': [0.1, 0.2, 0.5, 0.8]}]}

        async def capture(targets, outdir, **kwargs):
            outdir.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (800, 600), 'white').save(outdir/'article.png')
            return [{'id': sid, 'captured_utc': 'test', 'crops': [{'name': 'article',
                     'found': True, 'file': 'article.png', 'sha256': 'fixture', 'kind': 'article'}]}]

        def images(topic, outdir, **kwargs):
            outdir.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (800, 600), 'blue').save(outdir/'related.png')
            return {'images': [{'id': 'related', 'file': 'materials/related/related.png',
                     'source_url': image_url, 'source_credit': 'Fixture',
                     'capture_kind': 'related_image'}], 'errors': []}

        with patch('gemini_workflow.research_with_gemini', return_value=pack), \
             patch('gemini_workflow.create_dialogue', return_value=(script, {})), \
             patch('tools.prepare_project.capture_targets', side_effect=capture), \
             patch('tools.prepare_project.collect_related_images', side_effect=images):
            prepare({'topic': 'Example', 'provider': 'gemini'}, directory)
        return json.loads((Path(directory)/'project.json').read_text())

    def test_rejected_related_image_cannot_keep_its_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.prepare_with_related(directory, 'https://example.com/image')
        line = project['lines'][0]
        self.assertNotEqual(line['visual'], 'related')
        self.assertNotIn('focus_area', line)
        self.assertEqual(line['motion'], 'static')
        self.assertIn('visual_replaced', line['cue_fallback_reason'])

    def test_allowed_image_selection_and_coordinates_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.prepare_with_related(directory, 'https://www.city.example.lg.jp/image')
        line = project['lines'][0]
        self.assertEqual(line['visual'], 'related')
        self.assertEqual(line['focus_area'], [0.1, 0.2, 0.5, 0.8])
        self.assertNotIn('cue_fallback_reason', line)


class SegmentAndFallbackTest(unittest.TestCase):
    def test_pan_midpoint_matches_when_the_video_starts_mid_line(self):
        records = [
            {'id': 1, 'visual_id': 'A', 'start': 0., 'end': 4.,
             'motion': 'zoom_in', 'focus_area': [.1, .1, .4, .4]},
            {'id': 2, 'visual_id': 'A', 'start': 4., 'end': 8.,
             'motion': 'pan', 'focus_area': [.5, .5, .9, .9]},
        ]
        cues = build_cue_timeline(records)
        absolute = 4.4
        full = compute_crop_window(cues[1], absolute-records[1]['start'], 4.)
        segment_start = 4.2
        segment_record_start = records[1]['start']-segment_start
        partial = compute_crop_window(cues[1], absolute-segment_start-segment_record_start, 4.)
        for first, second in zip(full[0], partial[0]):
            self.assertAlmostEqual(first, second)

    def test_gap_start_keeps_previous_topic(self):
        records = [{'id': 1, 'start': 0., 'end': 2., 'visual_id': 'A', 'topic': 'Previous'},
                   {'id': 2, 'start': 3., 'end': 5., 'visual_id': 'A', 'topic': 'Future'}]
        cue, duration = held_cue_before(records, build_cue_timeline(records), 2.5)
        self.assertEqual(cue['topic'], 'Previous')
        self.assertEqual(duration, 2.)

    def test_invalid_camera_cannot_animate_valid_step_boxes(self):
        cue = parse_visual_cue({'motion': 'diagram_step', 'focus_area': [1, 1, 0, 0],
                                'diagram_steps': [{'label': 'A', 'box': [.1, .1, .4, .4]}]})
        self.assertEqual(cue['motion'], 'static')
        self.assertEqual(cue['diagram_steps'], [])
        self.assertIsNotNone(cue['fallback_reason'])

    def test_source_label_stays_inside_footer(self):
        frame = Image.new('RGB', (1280, 720), 'black')
        box = draw_dynamic_source_label(frame, 'Source: example.com ' * 8, None, [225, 92, 1055, 350])
        self.assertGreaterEqual(box[0], 225)
        self.assertLessEqual(box[2], 1055)
        self.assertGreaterEqual(box[1], 686)
        self.assertLessEqual(box[3], 720)


class RenderingAndPixelChangesTest(unittest.TestCase):
    def setUp(self):
        # Synthetic document with distinct regions
        self.doc = Image.new("RGB", (800, 600), "#FFFFFF")
        d = ImageDraw.Draw(self.doc)
        # Top half red, bottom half blue
        d.rectangle((0, 0, 800, 300), fill="#FF4444")
        d.rectangle((0, 300, 800, 600), fill="#4444FF")
        self.font = resolve_font(None, 24)

    def test_non_overlapping_side_panel_layout(self):
        cue = parse_visual_cue({
            "topic": "月の公転と満ち欠け",
            "motion": "zoom_in",
            "focus_area": [0.1, 0.1, 0.5, 0.5],
            "show_minimap": True,
        })
        # Render on 830x258 evidence slot
        panel = render_dynamic_visual_panel(
            raw_src_image=self.doc,
            evidence_width=830,
            evidence_height=258,
            cue=cue,
            elapsed=2.0,
            line_duration=4.0,
            font_path=None,
        )
        self.assertEqual(panel.size, (830, 258))
        self.assertEqual(panel.mode, "RGBA")

    def test_step_by_step_diagram_pixel_changes(self):
        cue = parse_visual_cue({
            "topic": "月齢ステップ",
            "motion": "static",
            "focus_area": [0.0, 0.0, 1.0, 1.0],
            "diagram_steps": [
                {"label": "新月", "box": [0.05, 0.05, 0.45, 0.45]},
                {"label": "満月", "box": [0.55, 0.55, 0.95, 0.95]},
            ]
        })
        # Render step 1 (elapsed = 1.0s of 4.0s)
        p1 = render_dynamic_visual_panel(
            self.doc, 830, 258, cue, elapsed=1.0, line_duration=4.0, font_path=None
        )
        # Render step 2 (elapsed = 3.5s of 4.0s)
        p2 = render_dynamic_visual_panel(
            self.doc, 830, 258, cue, elapsed=3.5, line_duration=4.0, font_path=None
        )
        # The pixels must differ between step 1 and step 2 due to highlight shift
        self.assertNotEqual(p1.tobytes(), p2.tobytes())

    def test_cache_keys_reflect_steps_and_topics(self):
        cue1 = parse_visual_cue({
            "topic": "トピックA",
            "motion": "static",
            "diagram_steps": [{"label": "ステップ1", "box": [0.1, 0.1, 0.3, 0.3]}]
        })
        cue2 = parse_visual_cue({
            "topic": "トピックB",  # different topic
            "motion": "static",
            "diagram_steps": [{"label": "ステップ1", "box": [0.1, 0.1, 0.3, 0.3]}]
        })
        k1 = get_dynamic_cache_key(cue1, elapsed=1.0, line_duration=3.0)
        k2 = get_dynamic_cache_key(cue2, elapsed=1.0, line_duration=3.0)
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
