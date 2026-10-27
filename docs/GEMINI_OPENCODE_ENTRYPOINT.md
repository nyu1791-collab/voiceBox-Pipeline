# Gemini と OpenCode の入口

## 使う入口

スマートフォンからは GitHub Actions の **Research and JSON Project** を実行する。
通常は次だけを入力する。

- `topic`: 調べるテーマ
- `source_urls`: 任意。記事または公開 YouTube URL を1行ずつ
- `minutes`: 会話尺の目安。達成条件ではない

`provider=gemini` が既定で、Gemini 3.8 Flash / MEDIUM が Google Search・URL Context を使い、資料・関連画像を集めた後に、ずんだもんと四国めたんの会話を作る。成果物は `source-pack-<run_id>`。動画はこの入口では作らない。

OpenCode から使うときも、`.opencode/skills/voicebox-research` が同じ `tools/prepare_project.py` を呼ぶ。OpenCode の旧 DeepSeek 台本 provider は、この Gemini 準備経路とは別の旧経路であり、新規の調査・会話作成には選ばない。

## 出力と継続

- `research.json`: Gemini が見つけた資料と根拠
- `materials/`: 撮影画面と関連画像、各出典
- `project.json`: 会話・資料・画像を結んだ制作入力
- `preparation.json`: 実際に得られた件数、撮影不能・画像取得不能の記録

資料数・画像数・尺は目安である。取れなかった資料は記録し、取れた資料と関連画像を使って会話作成を続ける。YouTube の確認画面は実フレームとは区別する。

Gemini の同じ要求は通信または一時応答の失敗時だけ、合計3回まで試す。成功した source pack は `resume_run_id` で再利用し、目的のない再調査・再生成・個別の監査工程は作らない。

## 守る範囲

秘密情報・Cookie・API キーをログ、成果物、Git に残さない。公開できない資料やアクセス制限を回避しない。動画化する場合のみ、既存の CPU VOICEVOX 10ワーカー経路へ `project.json` を渡す。GPU は使わない。
