# 承認から動画を作り、YouTubeへ非公開で載せる経路（2026-11-03）

状態を分けて書く。「作成・テスト済み」は実機の外部接続を含まない。

## 流れ

1. **テーマの承認（content-engine）** — `python -m engine video "テーマ" [--url https://...]` を実行する。Discordの「記事承認」に依頼が届く。携帯で「承認 記録ID」と返信すると、`media/jobs/video/<記録ID>.json`（status: approved）ができる。
2. **ジョブの確認（voiceBox-Pipeline）** — `python tools/video_job.py list --jobs ..\..\media\jobs\video` で承認済みのテーマを見る。費用は出ない。
3. **依頼の作成** — `python tools/video_job.py plan --job <json> --out <新しいフォルダ>` で `request.json` を作る。費用は出ない。
4. **調査・台本（Gemini）** — `python tools/video_job.py prepare --job <json> --out <新しいフォルダ> --run-paid`。Gemini の費用が出るため、`--run-paid` を付けた時だけ動く。内部は `tools/prepare_project.py`。
5. **音声・描画（Colab GPU）** — 未接続。`colab/zundamon_gpu_pipeline.ipynb` と `tools/colab_gpu_run.py` は実機確認前の下書き（[COLAB_GPU_DRAFT.md](COLAB_GPU_DRAFT.md)）。素材を Colab へ渡す方法とGPU実行の確認が残っている。
6. **YouTubeへ非公開で載せる** — `tools/youtube_upload.py`。既定は `private`。`public` は `--privacy public` を付けた時だけ。

## 新着から話題を選び、台本と素材を作る（feed-watch.yml、2026-11-04 改訂）

- **いつ動くか**：8時間ごと（日本時間 08:23・16:23・00:23）。変数 `FEED_WATCH_ENABLED` が `true` のときだけ定期実行する。手動実行（`workflow_dispatch`）はいつでも動く。`usage_only` を選ぶと、X APIの使用量だけを表示する。
- **何を見るか**：`config/feeds.json` の約106の情報源。報道・公式サイト・ネットの話題・ゲーム・エンタメ・政府機関のRSS（無料）と、X の公式アカウント10件（AI企業7件＋世界のニュース3件、X API）。
- **Xの読み方**：公式APIで、アカウント本人の投稿だけを読む。返信・リポスト・引用は除く（ほかの人の文章を含むため）。前回読んだ投稿より新しいものだけを読む（since_id）。読んだ投稿1件 0.005ドル。画像と動画は、投稿の情報に入っている配信先から取る（API課金の対象外と見込むが、次回の使用量で確かめる）。
- **判断**：Jev（OpenRouter）が新着を評価し、動画にする話題を1つ選ぶ。根拠は Web の記事2件以上（Xの投稿は根拠にしない）。選ばなければ何もしない。1日の作成数は `FEED_DAILY_MAX`（既定3）まで。
- **台本**：Gemini 3.8 Flash が、選ばれた話題の Web 記事（最大3件）から台本を作る（費用あり。Geminiの調査と台本の2回）。
- **素材**：選ばれた話題の根拠の画像・動画をダウンロードする（並列・画像30MB・動画100MB・1話題8件まで）。Xの投稿は、本文と画像から1枚のカードを作る（Xのページは撮らない）。
- **保存**：素材・カード・台本の下書きは、Artifactに入れない（このリポジトリは公開のため）。実行の最後に消す。動画制作の後は `feed_media.py discard` で破棄する。
- **まだつながっていない所**：音声（VOICEVOX）と動画の書き出し（Colab GPU）。台本の保存先は未決定（非公開の場所を決める）。
- **確認済み**：単体テスト78件、actionlint、このPCでのフィード取得・Jevの判断・台本作成（44行）・画像のダウンロード（41.9KB）・カード作成。GitHub上の台本とカードの工程は、まだ確かめていない。

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
