# Vertex AI / Antigravity GitHub connections

設定日：2026-10-20 UTC。設定に使用したGoogleアカウント：`n.yu17919191@gmail.com`。
対象：`nyu1791-collab/voiceBox-Pipeline`、Google Cloud `voice-box-512208`（238830383375）。

## Vertex AI

専用サービスアカウントは `github-vertex@voice-box-512208.iam.gserviceaccount.com`。
プロジェクトで `roles/aiplatform.user`、サービスアカウント上で対象リポジトリのWIF principalSetに `roles/iam.workloadIdentityUser` を付与。
既存provider `projects/238830383375/locations/global/workloadIdentityPools/-github-drive-sync/providers/-github-actions` を使用。
providerの条件は対象リポジトリかつ `refs/heads/main`。別ブランチからは認証できない。
既存のDrive用サービスアカウント・VM既定のGoogleログインは変更しない。

Actions Variables：`GCP_PROJECT_ID`、`GCP_SERVICE_ACCOUNT`、`GCP_WORKLOAD_IDENTITY_PROVIDER`、`GCP_LOCATION`（global）。
GitHub OIDCから短期トークンを取得し、長期サービスアカウント鍵やユーザーのOAuthファイルを保存しない。
モデル呼び出しを追加する場合はモデルID・入力・課金先を明示する。自動fallbackは追加しない。
モデル・リージョン・割当・請求状態ごとの実際の推論可否は、認証チェックだけでは確認できない。

## Antigravity CLI

専用Actions Secretは `ANTIGRAVITY_GEMINI_API_KEY`。
指定Googleアカウントで管理できるvoice-boxプロジェクトの既存Geminiキー（generativelanguage.googleapis.comに制限）をVMから直接登録した。
既存の `GEMINI_API_KEY` は保持する。このSecretは同じ既存キーの別名であり、独立した新規キーではない。

Actionsの実行ステップで `GEMINI_API_KEY: ${{ secrets.ANTIGRAVITY_GEMINI_API_KEY }}` として渡す。
ランナーの `~/.gemini/antigravity-cli/settings.json` に `modelProvider: gemini` を設定する。
CLIは公式配布1.4.3をSHA-512検証してランナー一時領域に取得する。VMの既存CLIやGoogleログイン設定は上書きしない。
これはGemini APIに対する認証であり、個人Googleログインの無料枠・Pro/Ultra枠を移すものではない。生成時の料金・制限はAPI側の条件による。
自動エージェント処理、commit/push、公開を追加したものではない。

## 手動チェック

GitHub Actionsの `Vertex and Antigravity Connections` を開き、mainで `Run workflow` を選ぶ。
このワークフローだけを起動し、動画・音声・Drive同期・SNS投稿は実行しない。

検証範囲：WIF短期トークン発行、Vertex APIの既存Model一覧の読取り（us-central1、0件でも成功）、Geminiモデル一覧の読取り、CLIの版とprovider設定。
生成API・Antigravityエージェントへのプロンプト送信は0回。
Artifactsの `google-connections-<run_id>-<attempt>` に、秘密を含まない `google-connections.json` を7日間保存する。
成功でもモデル推論やエージェント実行済みとは扱わない。失敗時はHTTPステータス・例外型のみを記録し、レスポンス本文・トークン・キーを出さない。

初回の接続確認39437291489ではWIF認証・Antigravity設定は成功。
Vertex確認に使ったService Usage APIの権限がVertex AI Userに含まれず403となったため、確認を本来のVertex APIの読取りへ修正した。追加の管理者権限は付与していない。
修正後の接続確認：**SUCCESS**。
- 実行：https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/39437630042
- 検証コード：`e8df726ae540170a41a9a44131a0d48fd631f4e4`
- GitHub OIDC → 専用サービスアカウントの短期トークン発行：成功。
- Vertex APIの既存Model一覧読取り（us-central1）：成功。
- 公式Antigravity CLI 1.4.3のチェックサム検証・導入・版確認・provider設定：成功。
- 専用Secretを使ったGemini APIモデル一覧読取り：成功。
- Artifact：`google-connections-39437630042-1` の `google-connections.json` を取得して内容確認済み。
- 生成API呼出し0回。Antigravityエージェントへの指示送信・動画・音声・公開処理は未実施。
- ローカルの検証では成功・失敗の記録、再試行なし、例外本文やテスト用秘密値がreceiptに入らないことを確認。

利用者からVMでの接続追加を指示された範囲で設定。常設のOwner/Editor権限は追加せず、Vertex AI Userと対象リポジトリのWIFだけを追加した。

公式：
- https://github.com/google-github-actions/auth
- https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines
- https://www.antigravity.google/docs/install?tab=cli
- https://www.antigravity.google/docs/getting-started
