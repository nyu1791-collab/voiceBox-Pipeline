# 引き継ぎ：Gemini と OpenCode の窓口

更新: 2026-10-28 JST  
対象: [PR #6](https://github.com/nyu1791-collab/voiceBox-Pipeline/pull/6) / `feat/gemini-research-dialogue-20261027`  
PR は `main`（`df6b569`）に対して 17 commit 先行、未マージ。

## 現在の入口

- スマートフォンからは GitHub Actions の **Research and JSON Project** を使う。
- 入力はテーマ、任意のURL、会話尺の目安だけ。既定は `provider=gemini`、`gemini-3.8-flash`、推論 `MEDIUM`。
- Gemini は Google Search / URL Context で資料を集め、撮影済み画面・関連画像・公開 YouTube URLを渡して、ずんだもんと四国めたんの会話を作る。
- OpenCode は `.opencode/skills/voicebox-research/SKILL.md` を通じて同じ Gemini 準備コマンドを使う。GitHub Actions から OpenCode は起動しない。
- Actions の選択肢から旧 `provider=opencode` を外した。旧 DeepSeek 専用コードは残っているが、新規の調査・会話作成の入口ではない。
- 一時的な Gemini 通信・応答失敗だけは、同じ要求を合計3回まで試す。再調査や再生成は目的があるときだけ行う。
- 準備成果物は `source-pack-<run_id>`。動画化は別の `voicevox-cpu-video.yml` で、CPU VOICEVOX 10ワーカーを使う。GPUは使わない。

## できていること

- 画像取得実行 [40575667666](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40575667666): 関連画像8件、出典ページを保存。
- 資料再利用＋会話生成 [40576734744](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40576734744): 会話16行、関連画像7件中3件を台詞に採用。
- YouTube撮影 [40566359864](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/runs/40566359864): ボット確認のため実フレームではなく `youtube_metadata` 画面を保存。

## 重要な扱い

資料・画像・尺の件数は目安であり、満たすまで止めない。取れなかった資料は `preparation.json` に残し、取得済みの資料・画像で続行する。画像出典を保存しても利用許諾の確認にはならない。

秘密情報・Cookie・APIキーをログ、成果物、Gitへ出さない。アクセス制限やYouTubeのボット確認は回避しない。成功していない撮影・動画化を成功として報告しない。

## 次の一手

依頼された実題材で、PR #6 の Actions または OpenCode スキルから Gemini 準備を1回実行し、生成された source pack の範囲を報告する。必要なときだけ、その `project.json` と素材を既存の CPU 動画フローへ渡す。
