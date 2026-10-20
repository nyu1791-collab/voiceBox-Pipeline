# 移行メモ：OpenCode無料DeepSeek台本API

更新：2026-10-20 UTC

対象リポジトリ：`nyu1791-collab/voiceBox-Pipeline` / `main`

作業基準コミット：`b07f4d9be420f2f7e30baf584a6ea4880a8607c3`

## 目的と依頼

利用者は「OpenCode Goで無料提供されているDeepSeek V4.1 Flash 1017をGitHubに登録する」よう依頼した。ここでの「登録」はAPI認証情報をGitHub Actions Secretに格納し、台本生成で選べるようにする意図と解釈した。利用者は作業を進めるよう明示している。

**再開時に読む正本は [`docs/RULES.md`](RULES.md) 全文。** `AGENTS.md`、`CLAUDE.md`、`CHATGPT.md` は同じ正本への入口であり、制作・編集ルールをそれぞれ複製していない。API接続が解決するまでは動画・音声・GPU生成を始めない。

## 完了したこと

- OpenCode公式ソースのモデル一覧と推論API文書を調べた。
- OpenCodeカタログには `deepseek-v4.1-flash-free` と有料版 `deepseek-v4.1-flash` が別モデルとしてある。無料版のカタログ価格は入力・出力とも0。無料版のAPIモデルIDは `deepseek-v4.1-flash-free`、OpenCode CLI上のIDは `opencode/deepseek-v4.1-flash-free`。
- VMに既存のOpenCode認証情報があることを確認し、その値を表示・作業ツリーへコピーせずに使った。
- GitHub Actions Secret `OPENCODE_API_KEY` を `nyu1791-collab/voiceBox-Pipeline` に登録した。GitHubから確認できたのはSecret名と更新日時（`2026-10-20T09:49:18Z`）だけ。値は取得・表示できず、リポジトリにも保存していない。
- 登録済みSecretを使って、公式カタログに載るOpenAI互換推論URLへ小さな応答確認を1回行った。HTTP **403** となり、生成成功は確認できなかった。エラー本文は保存していないため、原因を無料枠制限などと断定できない。
- 既存の台本生成コードはGeminiとGroqのみを許可し、GitHub workflowにもOpenCodeの選択肢・Secret受渡しはまだない。したがって、Secret登録は済んだが、**OpenCode台本生成の実装・動作確認は未完了**。

## モデル名と「1017」の扱い

調査した公式カタログで確認できた無料モデルIDは `deepseek-v4.1-flash-free` で、「1017」というIDやビルド指定は見つからなかった。カタログのrelease dateは `2026-09-10`。過去の別作業にある `deepseek1017` という名前は別経路のモデル指定であり、このOpenCode無料モデルと同一視しない。次の担当は「1017版を設定済み」と報告せず、利用者が指した表示と公式の現行IDの対応を、公式情報で解消する。

OpenCode公式資料では、OpenCode Goを低価格のサブスクリプションとして説明する箇所と、個別の無料モデルを列挙する箇所がある。モデル一覧の価格0表示だけでは、契約条件、レート制限、利用上限、今後の変更まで無条件に無料とは保証しない。

## 実行時点のリポジトリ状態

- GitHubの `main` とworkspaceのHEADは `b07f4d9`。API作業開始時のworkspaceはcleanだった。
- この移行文だけを今回新規追加している。既存アプリ、制作物、API実装は変更していない。
- 台本プロンプト作成、資料取得、JSON整形、台本生成の既存コードは `research.py` と `tools/prepare_project.py`。`research.py::generate_script()` は `gemini` / `groq` のみ受け付ける。
- 手動準備workflowは `.github/workflows/prepare-project.yml`。現状のprovider選択肢は `none`、`gemini`、`groq` で、Secretsは `GEMINI_API_KEY` と `GROQ_API_KEY` だけを環境へ渡す。
- 動画workflowは `.github/workflows/voicevox-cpu-video.yml`。入力ZIPに台本があればAPIは不要。`research-request.json` を自動準備する経路は `allow_script_api` と既存provider設定に依存し、OpenCodeはまだ配線されていない。
- 正本ルールではVOICEVOX CPU10並列、GPU/Colab不使用、台本APIは明示選択の1回呼出し、有料fallbackなし。今回の変更によりこれらを緩めていない。
- ActionsのSecret名は公開リポジトリから見えるが、Secret値は暗号化されGit内容・ログ・Artifactsへ出ないようにする。新しいコードでも環境変数をprintせず、例外本文やHTTP Authorizationヘッダーをログへ出さない。

## 参照した公式情報

- [OpenCode Consoleモデル一覧（公式リポジトリ）](https://github.com/anomalyco/opencode/blob/dev/services/www/src/docs/content/console/models.mdx) — 無料版・有料版の別ID、APIプロバイダーID、モデル価格メタデータ。
- [OpenCode Console推論API（公式リポジトリ）](https://github.com/anomalyco/opencode/blob/dev/services/www/src/docs/content/console/api/inference.mdx) — OpenAI互換URLと認証の説明。文書は無料チャットモデルをAuthorizationなしでも呼べると記載するが、今回の403の原因は特定できていない。
- [OpenCode Go（公式リポジトリ）](https://github.com/anomalyco/opencode/blob/dev/services/www/src/docs/content/console/go.mdx) — Goプランの説明。現行の条件は利用時にも公式ページで再確認する。
- [OpenCode公式プロバイダー実装](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/plugin/provider/opencode.ts) — CLI側のOpenCodeプロバイダー実装。CLIを使う場合は正規バイナリを使い、サービスを装う独自ヘッダーを作らない。

公式ソースは`dev`ブランチの文書で、将来更新される。今回の確認時点で読んだソースを示すリンクであり、API可用性や利用条件の永続保証ではない。

## 再開時の作業順

1. [`docs/RULES.md`](RULES.md) を全文読む。workflowと `research.py` の該当箇所を現在のHEADで読み直す。Secretや認証ファイルの値を表示しない。
2. OpenCode公式の現在の推論手順、無料モデルの利用条件、対象IDを確認し、403の原因を公式にサポートされた方法で切り分ける。以前と同じリクエストを無制限に再試行しない。`FreeTierError` と決めつけない。
3. サポートされた経路で最小の1リクエストを成功させ、選択したモデルID、終了状態、公式応答のコスト情報があればその値を記録する。HTTP 200だけを台本生成成功とみなさず、有効なJSONを一度返せたことまで確かめる。
4. 接続が解決した後、`research.py::generate_script()` にOpenCode providerを明示的に追加する。モデルを `deepseek-v4.1-flash-free` に固定または無料ID以外を拒否し、有料版へのfallback・自動再試行を作らない。使うOpenCode方式（公式APIまたは公式CLI）をコードと文書で一貫させる。
5. `.github/workflows/prepare-project.yml` に明示選択肢を加え、`OPENCODE_API_KEY` を選択時だけプロセス環境へ渡す。動画workflowから呼ぶ場合も、既存ZIP台本経路を壊さず、明示許可と同じ無料モデル制約を保つ。未選択時はAPIを呼ばない。
6. Secret欠落、モデルID不一致、有料ID拒否、API失敗時にfallbackしないこと、応答JSON検証を対象に必要なテストを追加する。実際に変更した範囲だけ検証する。
7. 差分を対象ファイルだけに限定して確認し、必要なら変更ファイルだけをコミット・pushする。GitHub Secret値、OpenCode auth、実台本・素材・生成物はコミットしない。

403の原因が解消できない間は、Secret登録済みという事実だけを引き継ぐ。動作する台本APIとして案内せず、既存の `none` / Gemini / Groq 経路を勝手に変更しない。

## 関連証跡の場所

VM上に保存した最小API試行の非秘密メタデータ：

```text
/home/n_yu1791/.local/state/voicebox-opencode-20261020/direct-api-probe.json
```

OpenCode CLIが認識していたカタログ表示の一時ファイル（VM `/tmp`、永続証跡ではない）：

```text
/tmp/opencode-registration-models-20261020.txt
```

VM上の既存OpenCode設定・認証ファイルには秘密が含まれ得る。中身を会話、GitHub、Artifacts、ログへ複製しない。GitHub Secretは値を読み戻さず、名前と更新メタデータだけを確認する。
