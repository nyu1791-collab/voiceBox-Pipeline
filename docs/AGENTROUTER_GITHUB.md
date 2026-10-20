# AgentRouterをGitHub Actionsから使う

画像で指定されたサービスは [agentrouter.org](https://agentrouter.org/) 。同名の `agent-router.net` や別のAPIサービスとキーを共有しない。

## 接続

GitHub Actionsの [AgentRouter Coding Connection](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/workflows/check-agentrouter.yml) を手動実行する。AgentRouterが対応クライアントとして案内する公式OpenCode CLI `opencode-ai@1.19.0` を一時起動し、短いPython関数の修正を1回だけ問い合わせる。

- 接続先：`https://agentrouter.org/v1`、OpenAI互換プロトコル。
- 認証：対象リポジトリのActions Secret `AGENTROUTER_API_KEY`。
- モデル：手動入力。既定値 `gpt-6.1-sol` は依頼画像に合わせた候補で、実際の利用可否はAgentRouterアカウントのモデル一覧と接続結果で確認する。
- GitHub権限：`contents: read`。checkoutの認証を保存せず、CLIへGitHubトークンや他社APIキーを渡さない。
- 成果物：`agentrouter-connection.json` をArtifactsへ7日保存。接続結果・選択モデル・CLI版・トークン数等だけを記録し、キー・モデルの生応答・プロバイダの生エラーは保存しない。

初回は [Secret追加画面](https://github.com/nyu1791-collab/voiceBox-Pipeline/settings/secrets/actions/new)へ、AgentRouterが発行したキーを保存する。既存の `OPENROUTER_API_KEY` や `OPENCODE_API_KEY` は別サービスのため転用しない。

利用者はVMでのキー発行・保存を許可している。VMブラウザーでAgentRouterの人間向けアクセス認証やログインが求められる場合、その操作が完了してから発行・保存を続ける。キーをチャットやスクリーンショットへ出さない。

## 実行と料金の扱い

明示選択したAgentRouterの1モデルだけを使い、別モデル・別ドメインへ自動切替しない。ツール要求、再試行、追加生成、未完了の応答を検知したらCLIを終了する。接続用のコード確認は台本生成・動画制作のproviderとは独立している。

OpenCodeの `catalog_cost` はサービス側の実請求額を確認したものではない。receiptの `billing_verified` は `false` とし、0という値でも無料・無制限を保証しない。[公式案内](https://co.agentrouter.org/portal/guide)と[公式の供給状況](https://agentrouter.org/api/status)には対応クライアント・アカウントごとの利用枠についての説明がある。定期実行、無制限再試行、自動課金は設定しない。

ローカルで明示的に確認する場合：

```bash
npm install --global opencode-ai@1.19.0
# AGENTROUTER_API_KEY は安全な方法で環境へ設定する。値は出力しない。
python tools/check_agentrouter.py --allow-api --model gpt-6.1-sol --output /private/agentrouter-connection.json
```

## 現在の状態

接続コードと手動workflowを用意し、ローカルIPCによる認証分離・選択モデルの検証・失敗時の秘匿をテストした。キー発行と実APIへの認証・生成確認は、VMで表示された `Access Verification` の本人操作待ち。発行・接続成功とはまだ報告しない。
