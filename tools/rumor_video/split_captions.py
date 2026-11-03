"""長い台詞を、字幕の枠に収まる長さで分ける。内容は変えない。
使い方: python tools/rumor_video/split_captions.py <project.json> [--limit 46]
元の project.json は .bak として残してから書き換える。"""
import argparse
import copy
import json
import re
import shutil
from pathlib import Path

DEFAULT_LIMIT = 46


def split_text(text, limit=DEFAULT_LIMIT):
    """文の切れ目（。）で分け、それでも長い文は読点（、）で分ける。"""
    pieces = []
    for sentence in re.split(r"(?<=。)", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > limit:
            cut = sentence.rfind("、", 0, limit)  # 読点まで含めても、枠の長さを超えない位置
            if cut <= 0:
                pieces.append(sentence[:limit])
                sentence = sentence[limit:]
            else:
                pieces.append(sentence[:cut + 1])
                sentence = sentence[cut + 1:]
        pieces.append(sentence)
    return pieces


def split_project(project, limit=DEFAULT_LIMIT):
    rows = []
    for line in project["lines"]:
        for part in split_text(line["text"], limit):
            row = copy.deepcopy(line)
            row["text"] = part
            rows.append(row)
    for index, row in enumerate(rows, start=1):
        row["id"] = index
    project["lines"] = rows
    return project


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    shutil.copy2(args.project, args.project.with_suffix(".json.bak"))
    project = json.loads(args.project.read_text(encoding="utf-8"))
    project = split_project(project, args.limit)
    args.project.write_text(json.dumps(project, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"lines: {len(project['lines'])}, longest: {max(len(r['text']) for r in project['lines'])}")


if __name__ == "__main__":
    main()
