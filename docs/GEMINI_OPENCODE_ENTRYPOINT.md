# Gemini と OpenCode の入口

## 使う入口

スマートフォンからは GitHub Actions の **Research and JSON Project** を実行する。
通常は次だけを入力する。

- `topic`: 調べるテーマ
- `source_urls`: 任意。記事または公開 YouTube URL を1行ずつ
- `minutes`: 会話尺の目安。達成条件ではない

`provider=gemini` が既定で、Gemini 3.8 Flash / MEDIUM が Google Search・URL Context を使い、資料・関連画像を集めた後に、ずんだもんと四国めたんの会話を作る。成果物は `source-pack-<run_id>`。動画はこの入口では作らない。

GitHub ActionsのGemini調査・台本・TranscribeはVertex AIを使用する。既定プロジェクトは `project-fdadb4dd-cb77-4270-9cd`、GitHub OIDCと専用サービスアカウントで認証する。初回は対象プロジェクトで `bash tools/setup_vertex_wif.sh` を実行する。別プロジェクトを使うときは `GOOGLE_CLOUD_PROJECT` を指定してセットアップし、Actions変数 `VOICEBOX_VERTEX_PROJECT`、`VOICEBOX_WIF_PROVIDER`、`VOICEBOX_VERTEX_SERVICE_ACCOUNT` に出力値を登録する。実際の請求は、そのプロジェクトにリンクしたCloud Billingアカウントに適用される。クレジットの対象・残額はCloud Billingで確認する。ローカルでも `VOICEBOX_VERTEX_PROJECT` とApplication Default Credentialsを指定する。APIキー経路を使う場合だけ `VOICEBOX_GEMINI_ROUTE=api_key` を明示し、`GEMINI_API_KEY` を設定する。Vertexの設定・認証失敗時に自動で切り替えない。経路・モデル・Vertexのプロジェクトとリージョンはログに残す。

OpenCode から使うときも、`.opencode/skills/voicebox-research` が同じ `tools/prepare_project.py` を呼ぶ。OpenCode の旧 DeepSeek 台本 provider は、この Gemini 準備経路とは別の旧経路であり、新規の調査・会話作成には選ばない。

## 出力と継続

- `research.json`: Gemini が見つけた資料と根拠
- `materials/`: 撮影画面と関連画像、各出典
- `project.json`: 会話・資料・画像を結んだ制作入力
- `preparation.json`: 実際に得られた件数、撮影不能・画像取得不能の記録

資料数・画像数・尺は目安である。取れなかった資料は記録し、取れた資料と関連画像を使って会話作成を続ける。画像が1枚も取得できない場合は無地の背景を使い、撮影した資料画像として数えない。YouTube の確認画面は実フレームとは区別する。資料そのものを得られない場合や台本・音声を作れない場合は、原因を記録して停止する。

Gemini の同じ要求は一時的な通信障害・HTTP 429・5xxで合計3回まで試す。読み取れない応答は再送せず失敗として記録する。401・403などの認証や入力エラーは繰り返さない。成功した source pack は `resume_run_id` で再利用する。尺の目安だけ変更した場合も資料を再利用でき、`regenerate_dialogue` を指定すると新しい目安で台本を作る。目的のない再調査・再生成・個別の監査工程は作らない。

## 守る範囲

秘密情報・Cookie・API キーをログ、成果物、Git に残さない。公開できない資料やアクセス制限を回避しない。動画化する場合のみ、既存の CPU VOICEVOX 10ワーカー経路へ `project.json` を渡す。GPU は使わない。
