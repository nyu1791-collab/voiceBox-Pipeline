# 引き継ぎ：Gemini と OpenCode の窓口

更新日：2026-10-27 UTC。対象は `nyu1791-collab/voiceBox-Pipeline` の [PR #6](https://github.com/nyu1791-collab/voiceBox-Pipeline/pull/6)、ブランチ `feat/gemini-research-dialogue-20261027`。変更は `main` に未マージ。旧 `MIGRATION_*_20261020.md` は当時の記録として扱い、この文書を現状の引き継ぎに使う。

## 利用者の希望

- パソコンは持っていない。スマートフォンから利用できるよう、操作や接続の作業は可能な限り担当側で行う。
- Gemini 3.8 Flash の推論 MEDIUM で YouTube やサイトの情報を集め、関連画像やスクリーンショットを用いて、ずんだもんとめたんの会話を作る。尺や件数は目安。出典を残す。
- 不要な監査、厳格な停止条件、確認の繰り返し、手戻りを減らし、簡潔で素早い処理にする。TPS 測定は不要。
- 音声・動画生成は既存方針どおり CPU の VOICEVOX 10 ワーカーを使う。

## 実際の入口

1. GitHub Actions の `prepare-project.yml` は手動実行の準備入口。既定の `provider=gemini` は `tools/prepare_project.py` から `gemini_workflow.py` を直接呼ぶ。Gemini が Google Search / URL Context で情報を集め、次の呼び出しで資料画像と公開 YouTube URL を渡して会話を作る。既定モデルは `gemini-3.8-flash`、推論は `MEDIUM`。成果物は動画ではなく `source-pack-<run_id>`。
2. 画像検索は `image_search.py` による DDGS の画像検索で、Google 画像検索そのものではない。画像と出典ページの URL を保存し、利用時は動画内に出典ドメイン、説明文に URL を残す。出典表示だけで画像利用の許諾が得られるわけではない。
3. `.opencode/skills/voicebox-research/SKILL.md` は OpenCode に上記 Gemini 準備処理を使わせるためのスキル。OpenCode CLI でスキルが検出されることは確認済みだが、OpenCode エージェントがこのスキルを使って Gemini を実際に呼んだことは未確認。Gemini の Actions 入口は OpenCode を自動起動しない。
4. 既存の `provider=opencode` は別経路で、`opencode_script.py` から `deepseek-v4.1-flash-free` に台本を依頼する。Gemini を呼ぶスキル経由のハーネスではなく、エージェントのツール使用も無効。旧経路にはモデルや権限などの停止条件が残っており、迅速化の希望に沿って必要な範囲を整理する余地がある。OpenCode Go の契約状態や無料モデルの将来の利用可能性は確認していない。
5. `voicevox-cpu-video.yml` は実動画作成の入口。今回の変更で新しい経路から最後まで動画を生成した実績はまだない。

## 確認済みの実行と限界

- [画像のみの実行 40575667666](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40575667666)：関連画像 8 件、エラー 0 件、出典ページを保存。
- [資料の再利用と会話生成 40576734744](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40576734744)：先行実行の資料を再利用し、Gemini が会話 16 行を生成。関連画像 7 件中 3 件が会話に選択された。[成果物](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40576734744/artifacts/12766034381) の保存期限は 2026-11-03 20:19 UTC。先行実行の取得結果にはサイト 4 件の HTTP 403 と画像 1 件の取得エラーが残る。Unicode URL の修正はローカルで確認済みだが、当該画像の再取得はしていない。
- [YouTube 撮影 40566359864](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40566359864)：YouTube の再生がボット確認で遮られ、タイトル等の `youtube_metadata` 画面を保存した。実際の動画フレーム取得成功とは扱わない。公開 YouTube URL を Gemini に渡す処理の成功も、動画の内容を完全に理解できた証明ではない。
- `YOUTUBE_STORAGE_STATE` は任意の GitHub Actions シークレットとして YouTube のブラウザ撮影に使える設計。ただし現時点の設定有無や、スマートフォンだけで安全に作成する手順は未確認。403 やボット制限の解消を約束しない。
- 既存の対象テストは前回ローカルで 13 件成功。今回の確認環境では `pytest` コマンドがなく、テストを再実行できていない。新たなモデル呼び出しや動画生成も行っていない。

## 次の担当者へ

1. OpenCode のスキル経由で Gemini に到達する入口を、既存の DeepSeek 専用 `provider=opencode` と混同せずに整える。GitHub Actions 上で Gemini の認証を必要なプロセスにだけ渡し、秘密情報をログや成果物に出さない。無料枠や課金状況は契約に依存する。
2. 利用者の依頼内容に沿う題材で、OpenCode → スキル → Gemini → 資料・会話まで一度つなぎ、成功した範囲を報告する。呼び出しの追加や再生成を目的なく繰り返さない。
3. YouTube が遮られた場合は実フレームと代替画面を区別して記録し、スマートフォン利用を前提に、必要な接続方法を検討する。画像の出典を保持し、取得できなかったサイトは取得できたと扱わない。
4. 動画生成まで求められた場合は、資料の再利用を優先し、必要な素材をそろえて CPU の既存フローにつなぐ。現状は PR ブランチであり、`main` への反映状態を取り違えない。

この作業環境のローカル `HEAD` は `main` の `df6b569` で、作業ツリーに既存の未コミット変更がある。まとめて追加・破棄・リセットしない。GitHub の PR ブランチは別途更新されているので、次の編集前に対象ブランチの最新状態を確認する。
