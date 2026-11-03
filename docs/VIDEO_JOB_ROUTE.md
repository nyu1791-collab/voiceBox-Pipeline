# 承認から動画を作り、YouTubeへ非公開で載せる経路（2026-11-03）

状態を分けて書く。「作成・テスト済み」は実機の外部接続を含まない。

## 流れ

1. **テーマの承認（content-engine）** — `python -m engine video "テーマ" [--url https://...]` を実行する。Discordの「記事承認」に依頼が届く。携帯で「承認 記録ID」と返信すると、`media/jobs/video/<記録ID>.json`（status: approved）ができる。
2. **ジョブの確認（voiceBox-Pipeline）** — `python tools/video_job.py list --jobs ..\..\media\jobs\video` で承認済みのテーマを見る。費用は出ない。
3. **依頼の作成** — `python tools/video_job.py plan --job <json> --out <新しいフォルダ>` で `request.json` を作る。費用は出ない。
4. **調査・台本（Gemini）** — `python tools/video_job.py prepare --job <json> --out <新しいフォルダ> --run-paid`。Gemini の費用が出るため、`--run-paid` を付けた時だけ動く。内部は `tools/prepare_project.py`。
5. **音声・描画（Colab GPU）** — 未接続。`colab/zundamon_gpu_pipeline.ipynb` と `tools/colab_gpu_run.py` は実機確認前の下書き（[COLAB_GPU_DRAFT.md](COLAB_GPU_DRAFT.md)）。素材を Colab へ渡す方法とGPU実行の確認が残っている。
6. **YouTubeへ非公開で載せる** — `tools/youtube_upload.py`。既定は `private`。`public` は `--privacy public` を付けた時だけ。

## RSSの新着から話題を選ぶ（feed-watch.yml、2026-11-03）

- `.github/workflows/feed-watch.yml` が毎時23分に `config/feeds.json` のRSS・RSSHub経路を見る（`tools/feed_watch.py`）。既読の記録はActionsのキャッシュ `feed-state-*`。初回は今ある記事を既読にするだけ。
- 新着があれば、Gemini（`auto_topic.py --candidates`）が新着を読み、動画にする価値がある話題を1つ選ぶ。無ければ選ばない。一覧の記事URLに加えて別の根拠を検索で探し、検索の転送用リンクは実際の出典URLに直す。1日の作成数は変数 `FEED_DAILY_MAX`（既定3）まで。
- その後は video-auto.yml と同じく `video_job.py plan` まで（費用なし）。手順4以降の接続は video-auto と同じ未接続の状態。
- 定期実行はリポジトリ変数 `FEED_WATCH_ENABLED` を `true` にした時だけ動く。video-auto と同じ並び（concurrency）なので同時には選ばない。
- 確認済み: 単体テスト、actionlint、このPCでの実フィードの取得（9件・162記事）、新着8件からGeminiが話題を選びジョブを作るまで。

## 承認が必要な所

- テーマの承認（手順1）と、動画の公開は別。公開は承認しない限り行わない。
- YouTubeの公開は、非公開で確認してから利用者が手で切り替える想定。

## 利用者がする手順

1. Google Cloud で YouTube Data API v3 を有効にし、OAuthクライアント（デスクトップアプリ）を作る。JSONを `secrets\youtube_client.json` に保存する。
2. `python tools/youtube_upload.py auth --client-secret ..\..\secrets\youtube_client.json --token ..\..\secrets\youtube_token.json` を実行し、表示されたURLをブラウザで開いて許可する。
3. Colabで `colab/zundamon_gpu_pipeline.ipynb` を開き、GPUのランタイムに接続してから実行する（実行は依頼があったときだけ）。

## 未確認

- content-engine の承認→ジョブ作成は、テストでは確認したが、実際のDiscordへの送信は行っていない。
- `prepare`（Gemini）は未実行。
- Colab GPU の音声・描画は未接続・未確認。
- YouTubeへのアップロードは、テスト（通信を置き換えたもの）だけ。実際の認証とアップロードは未確認。
- 承認の判定は、今は `python -m engine approve` を手で実行したときだけ動く。自動化（定期実行）は未設定。

## 保存先

- 承認済みの動画テーマ: `media/jobs/video/`（Gitに入れない）
- 認証のJSONとトークン: `secrets/`（Gitに入れない。値は表示しない）
- 調査・台本・動画: `media/projects/`（Gitに入れない）
