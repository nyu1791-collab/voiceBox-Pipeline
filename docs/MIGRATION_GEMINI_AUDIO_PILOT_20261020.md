# 移行メモ：Gemini台本からVOICEVOX CPU音声までの最小パイロット

更新日：2026-10-20  
対象リポジトリ：[`nyu1791-collab/voiceBox-Pipeline`](https://github.com/nyu1791-collab/voiceBox-Pipeline)  
作業ブランチ：`main`（このメモの作成時点で作業ツリーに未コミット変更なし）

## 目的

まず `voiceBox-Pipeline` で、短いテーマ入力からGeminiで台本JSONを作り、公式VOICEVOX CPU版で音声を生成してArtifactsに保存する、手動起動の最小パイロットを完成させる。動作を確かめてから、再利用できるActionsの型を作り、別ジャンルのリポジトリへ展開する。

初回パイロットには動画編集、SNS投稿、YouTube公開、アフィリエイト、決済、サイト公開を含めない。外部への公開・送信は別途明示的に実装する。

## 合意した進め方

1. 既存リポジトリで「入力 → Gemini台本生成 → VOICEVOX音声 → 成果物」の細い一経路を仕上げる。
2. 失敗時の扱い、秘密情報、Artifactsを含めて再現可能なActionsの形にする。
3. 最小パイロットを実行して成果物を確認する。
4. 成功した型をテンプレート化し、データ集計ポータルなど別ジャンルへ一つずつ広げる。
5. 各リポジトリは独立して起動・停止できる構成にする。複数の全自動ジョブや収益化を初回から一括で作らない。

## 現在確認できた状態

- `main` は `origin/main` と一致している。
- Draft PR [#1](https://github.com/nyu1791-collab/voiceBox-Pipeline/pull/1)「Add Gemini assisted GitHub change workflow」は `main` 向けに開いている。これはGeminiを使ったコード変更提案の作業であり、台本生成から音声出力までのパイプラインではない。今回のパイロットと混同しない。
- `.github/workflows/prepare-project.yml` は手動起動でき、Gemini/Groqを明示選択して情報収集・プロジェクト素材の準備を行うが、音声を生成しない。
- `.github/workflows/voicevox-cpu-video.yml` はデモ動画、または提供ZIPから動画を作る。デモ台詞は固定で、productionには素材ZIPと入力資産が必要。したがって、いずれも現状のままでは「短い入力からGemini台本と音声を得るだけ」の最小パイロットではない。
- リポジトリの規則は `docs/RULES.md`。VOICEVOX公式CPU版を使い、GPU/Colabを使わず、台本APIは選択したものを明示的に1回呼び、別モデルへ自動fallbackしない。生成した台本・素材・認証情報をGitへコミットしない。
- 会話中の画面ではGeminiアプリのGitHubコネクタが接続済みと表示されていた。これはGeminiアプリからGitHubのコードを扱うための接続であり、Gemini APIキー、Cloud Shellのログイン、GitHub Actionsの実行権限を相互に付与するものではない。
- Cloud Shellでは対象Googleアカウントで `gcloud` の認証が完了し、GitHub Actions用Workload IdentityからサービスアカウントへのIAMバインディング更新が成功した画面が確認されている。これは設定したGoogle Cloudリソースへの連携であり、GeminiアプリやGitHubアカウントに包括的な権限を与える設定ではない。
- リポジトリのActions Secrets一覧では `GEMINI_API_KEY`、`YOUTUBE_API_KEY`、`OPENCODE_API_KEY` の名前が確認できた（値は表示されていない）。
- Google Cloudの `voice-box-512208` プロジェクトでは、現在のYouTube Data APIキーが会話に表示された旧キーとは別に2026-10-20 11:23 UTCに作成され、`youtube.googleapis.com` のみにAPI制限されている。GitHubの `YOUTUBE_API_KEY` Secretも同時刻に更新されている。キー値は取得・表示していない。
- 手動検索ワークフロー `.github/workflows/youtube-research.yml` と `tools/youtube_search.py` を追加。公開動画の検索結果を最大5件JSON Artifactに保存し、自動実行・投稿はしない。YouTube `search.list` は1回100クォータ単位を使う。
- PR [#3](https://github.com/nyu1791-collab/voiceBox-Pipeline/pull/3) はCI成功後にmainへマージ済み。Actions run `39429835341` を `VOICEVOX`・1件で実行し、成功・Artifact取得・JSON形式・APIキー非掲載を確認した。

## 次に実装する内容

新しい手動起動のActionsワークフローを追加する。既存の動画ワークフローとは分け、最初は短いテーマまたは入力文を受け取って次を行う。

1. Gemini APIを選択したモデルIDで1回呼び、所定スキーマの台本JSONを生成する。
2. 台本の必須項目、話者、長さを検証し、不正な応答なら失敗として止める。無断で再生成・別モデルへ切替えない。
3. リポジトリの既定に従い、VOICEVOX公式CPU版を独立した10エンジン・各1スレッドで利用する。GPUは使わない。
4. Actions Artifactsに `script.json`、`narration.wav`、`cpu-voice-receipt.json` を保存する。音声は48kHz mono PCM16とし、実行記録に使用エンジンと終了状態を残す。
5. 成功・失敗を正確に記録し、起動したVOICEVOXエンジンをどちらの場合も終了する。認証値や台本をリポジトリへコミットしない。

必要な権限は読み取り中心とし、GitHub Actionsの `contents: read` から始める。Gemini APIキーは対象リポジトリのActions Secret `GEMINI_API_KEY` に保存し、ログ・Artifacts・ソースに出さない。出力はまずActions Artifactsだけに保存する。

## 完了の確認条件

- 手動実行に入力した短いテーマから、検証可能な `script.json` と再生できるWAVが得られる。
- WAVが48kHz mono PCM16であること、実行記録にCPU版VOICEVOXと並列数が残ることを確認する。
- 成功時・失敗時のどちらでも、この実行が起動したVOICEVOXプロセスが終了する。
- APIキー、GitHubトークン、ユーザーの認証情報がログ・Artifacts・コミットへ含まれない。
- 自動投稿や公開を行わず、結果はArtifactsで取得できる。

## 認証と秘密情報の扱い

- 会話中にCloud Shell出力へ旧YouTube APIキーの値が表示されていた。Google Cloudの有効キー一覧では別IDのYouTube制限付きキーのみ確認でき、GitHub Secretも新キー作成直後に更新されている。旧キー値はこの文書にもGitにも転記していない。
- Gemini APIをActionsから使う際は、GeminiアプリのGitHubコネクタ接続だけでは足りない。Google AI Studio等で取得したGemini APIキーを、リポジトリのActions Secret `GEMINI_API_KEY` として別途登録する。
- 音声パイロットの初回経路にYouTube APIは不要だが、別の手動YouTube検索 workflow は接続・実行確認済み。YouTube自動投稿や新たな検索API接続は含まない。具体的な機能が必要になった段階で、利用規約、課金、必要権限、キー制限を確認する。
- GitHub CLIのOAuth権限追加やCloud IAMの変更は、その用途に必要な範囲で管理する。「全権限」を常設したり、キーやサービスアカウント資格情報をGitへ保存したりしない。

## 次の作業

1. このメモを起点に、既存の `docs/RULES.md` と実装を読み、パイロット用ワークフローの入出力スキーマを決める。
2. Gemini呼び出しとVOICEVOX音声生成をつなぐ手動Actionsを実装する。既存の制作・動画ワークフローを壊さず、必要な箇所だけを変更する。
3. `GEMINI_API_KEY` がActions Secretに設定されていることを値を表示せずに確認し、短い入力で実行する。
4. 上記の完了条件を成果物と実行ログで確認し、その結果を記録してから、テンプレート化や次のジャンルへ進む。

## 次の会話へ引き継ぐ依頼文

> `voiceBox-Pipeline` でGemini台本生成からVOICEVOX公式CPU版の音声出力までの最小パイロットを仕上げてください。まず `AGENTS.md` と `docs/RULES.md` 全文、このメモを読み、既存ブランチと差分を確認してください。手動起動のActionsでテーマ入力 → 選択したGeminiモデルを1回だけ呼び出して台本JSONを生成・検証 → CPU版VOICEVOX10エンジン（各1スレッド）で48kHz mono PCM16 WAVを生成 → 台本・WAV・CPU実行記録をArtifactsへ保存する構成にします。GPU/Colab、自動fallback、自動公開、YouTube投稿、収益化機能は追加しません。秘密情報は出力・コミットせず、APIキーはActions Secretから読み込みます。実装後、短い入力で実行して成果物とエンジン終了を確認し、実際の結果を記録してください。Draft PR #1はコード変更提案用で、この音声パイロットとは別作業です。`
