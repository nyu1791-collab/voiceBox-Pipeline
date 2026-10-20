# 移行メモ：OpenCode無料DeepSeek 1017 台本接続

更新：2026-10-20 UTC

対象：`nyu1791-collab/voiceBox-Pipeline` / `main`

## 目的と確定したモデル

無料のDeepSeek 1017をOpenCode経由で台本生成へ接続する。利用者は実装・検証を明示承認済み。正本は [RULES.md](RULES.md) 全文。

利用者が提供したOpenCode公式投稿の画像で、DeepSeek V4.1 FlashはGo・Freeとも1017へ更新済み、利用者側の設定変更は不要と確認した。APIの無料IDは `deepseek-v4.1-flash-free`、CLI表記は `opencode/deepseek-v4.1-flash-free` のまま。架空の `-1017` IDや有料版には置き換えない。

- [公式更新告知](https://x.com/opencode/status/2112220734768779441)
- [公式モデル一覧](https://github.com/anomalyco/opencode/blob/dev/services/www/src/docs/content/console/models.mdx)

無料提供は期間限定。Goの有料契約・有料モデルを追加する作業ではない。

## 接続診断の結果

- 以前のSecret登録は完了済み。今回もGitHub側で `OPENCODE_API_KEY` の名前と更新日時 `2026-10-20T09:49:18Z` を確認した。値の読み戻し・表示・Git保存はしていない。
- Python標準HTTPクライアントによるモデル一覧GETは `403`、Cloudflareの `error code: 1010`。これだけでキー無効や無料枠超過とは断定しない。
- 公式文書のcurl方式で認証付き推論を1回試すと `403 / FreeTierError`。無料クライアント制限があり、直接API経路は不採用。
- 公式CLI `1.19.0` の標準設定で最小応答 `{"ok":true}` の成功を確認した。全ツールを無効化した構成では同じFreeTierErrorになった。公式リポジトリにも同型の [報告](https://github.com/anomalyco/opencode/issues/50627) がある。
- 公式CLIのローカルサーバーを用いる実装で、架空資料から両話者の2台詞を生成し、JSON・資料ID・話者・正常終了を検証できた。生成1回、カタログ上のコスト0、実時間8.377秒。カタログのコスト情報を請求明細と同一視しない。

## 実装

- `research.generate_script()` のproviderに `opencode` を追加。明示許可・正確な無料ID・認証が必須。Gemini/Groqの処理は保持する。
- `opencode_script.py` は検証済み公式CLI `opencode-ai@1.19.0` を使い、認証付きのloopbackローカルサーバーへ接続する。推論ゲートウェイのクライアント識別情報は偽装しない。
- HOME・XDG領域・セッションを一時ディレクトリへ隔離し、選択したOpenCodeキーと通信設定以外の認証を継承しない。利用者の既存設定・プラグイン・認証ファイルは変更しない。
- ツールは承認せず、ツール要求・再試行待ち・追加生成・別モデル・非ゼロのカタログコスト・不完全応答を検知したら停止する。終了・失敗・タイムアウトの全経路で起動したプロセス群を終了する。自動のタイトル生成・要約・圧縮・共有は無効。
- 手動準備workflowへ `opencode` 選択肢を追加。選択したproviderのSecretだけを渡す。
- 動画workflowは `allow_script_api:true` と `script_provider:opencode` を必要とし、ZIP内のproviderとの一致も検証する。既存Gemini/Groqは `script_provider:request` のまま。提供台本ではLLMを呼ばない。
- 手動の `OpenCode Free Connection` workflowと `tools/check_opencode.py` を追加。架空資料から短い台本を1回検証し、非秘密receiptだけ保存する。記事取得・撮影・音声・動画は実行しない。

## 検証結果と現在の状態

- 新規の18テストと既存のAPI許可テストが成功。無料ID制約、認証欠落、モデル不一致、不正JSON、ツール・再試行停止、プロセス終了、認証隔離、提供台本・none経路、動画側の明示選択を確認した。
- 実装は `af5c6a75bdff06a600154a537b91fcb946236684` として `main` に反映済み。
- 登録済みSecretを使った [GitHub Actions接続確認](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/39407302165) は成功。2026-10-20 10:21 UTC、生成1回、両話者2台詞、JSON・資料ID・正常終了の検証成功、カタログ上のコスト0。接続確認処理は6.377秒。公式CLIは `1.19.0`。音声や動画の生成時間ではない。
- 非秘密receiptを [Artifact](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/39407302165/artifacts/12256878449) に保存済み。保存期間7日。トークン記録はinput 9524、output 126、reasoning 254、cache read 1664、total 11568。
- [結合テストCI](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/39407298708) も成功。`pytest` は新規18件を含め32件成功、テスト実行31.72秒。既存の本文取得・実ブラウザー撮影・字幕・メディア編集も通過した。
- 接続とworkflow配線の未完了作業はない。本番テーマによる台本・全編動画の制作は別の入力を使う次の作業。
- 今回は動画・音声・GPU・Colabを起動していない。有料fallback、外部への自動公開、APIキーの再登録・削除は行っていない。

## VM側の非秘密証跡

`/home/n_yu1791/.local/state/voicebox-opencode-20261020/` に `curl-api-probe.json`、`official-cli-standard-probe.json`、`server-script-check-v2.json` を保存した。旧 `direct-api-probe.json` は過去の失敗結果であり、現在の接続成功と混同しない。
