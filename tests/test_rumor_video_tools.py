import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "rumor_video"))

import make_short  # noqa: E402
import split_captions  # noqa: E402


class SplitCaptionsTests(unittest.TestCase):
    def test_short_text_is_kept_as_one_piece(self):
        self.assertEqual(split_captions.split_text("短い台詞です。"), ["短い台詞です。"])

    def test_sentences_are_split_at_the_maru(self):
        self.assertEqual(split_captions.split_text("一つ目です。二つ目です。"), ["一つ目です。", "二つ目です。"])

    def test_long_sentence_is_split_at_a_comma_and_never_exceeds_the_limit(self):
        text = "次に、OpenAIの社員とされるアカウントの投稿です。2026年11月3日の投稿では、「GPT-6 Astra」と「GPT-6.1 Terra」を含む、dotsを動かすモデルの家族を広げている、と書かれていますわ。"
        pieces = split_captions.split_text(text, 46)
        self.assertTrue(all(len(piece) <= 46 for piece in pieces))
        self.assertEqual("".join(pieces), text)  # 内容は変えない

    def test_split_project_renumbers_ids_and_keeps_fields(self):
        project = {"lines": [{"id": 9, "speaker": "四国めたん", "text": "一つ目です。二つ目です。", "visual_id": "v1"}]}
        result = split_captions.split_project(project)
        self.assertEqual([row["id"] for row in result["lines"]], [1, 2])
        self.assertTrue(all(row["visual_id"] == "v1" and row["speaker"] == "四国めたん" for row in result["lines"]))


class MakeShortTests(unittest.TestCase):
    def test_command_uses_vertical_canvas_and_the_requested_length(self):
        command = make_short.build_command("a.mp4", Path("out.mp4"), "f.ttf", "h.txt", "p.txt", seconds=58)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("s=1080x1920", graph)
        self.assertIn("d=58", graph)
        self.assertIn("textfile=h.txt", graph)
        self.assertIn("textfile=p.txt", graph)
        self.assertEqual(command[command.index("-t") + 1], "58")

    def test_font_and_text_are_passed_as_files_not_inline(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "h.txt"
            text.write_text("「見出し」\n2行目", encoding="utf-8")
            command = make_short.build_command("a.mp4", Path(tmp) / "o.mp4", "f.ttf", text.name, "p.txt")
            graph = command[command.index("-filter_complex") + 1]
            self.assertNotIn("「見出し」", graph)


if __name__ == "__main__":
    unittest.main()
