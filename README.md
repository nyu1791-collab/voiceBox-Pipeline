# voiceBox Pipeline

**GitHub ActionsにVOICEVOX CPU版を搭載し、10プロセス・各1スレッドで音声を作ります。** GPU・Colab・有料生成APIは使いません。MoviePy・CairoSVG・FFmpegを実際の編集処理へ組み込みました。

```mermaid
flowchart LR
  R[テーマ・RSS・検索・指定URL] --> S[記事本文・資料ID]
  S --> T[提供JSON / 明示選択した台本API]
  T --> U[Playwright並列撮影・素材台帳]
  U --> A[提供ZIP / ランナー一時領域]
  A --> B[VOICEVOX公式CPU版 × 10]
  B --> C[実音声の時刻・字幕・立ち絵]
  C --> D[MoviePy動画・CairoSVG図解・FFmpeg音声]
  D --> E[MP4・SE配置記録・サムネイル → Artifacts]
```

## GitHubで実行

[Actions → VOICEVOX CPU10 Video](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/workflows/voicevox-cpu-video.yml) → **Run workflow**。

| mode | 動作 |
|---|---|
| `demo` | 入力不要。公式VOICEVOXで異なる10台詞をCPU合成し、MoviePyの切り抜き・クロスフェード、CairoSVG、BGMダッキングを実行してMP4を残す機能デモ。正規立ち絵を使うニュース制作とは別。 |
| `production` | 提供ZIPから、ずんだもん・四国めたんの正規立ち絵・字幕・口パクを含む動画を制作する。 |

VOICEVOXは[公式CPU版0.25.2](https://github.com/VOICEVOX/voicevox_engine/releases/tag/0.25.2)をSHA-256付きで取得。10個のプロセスが同じ本体を参照します。初回の約1.8GBダウンロードと展開後は、Actionsキャッシュを利用します。各実行の終了時には起動したエンジンを終了します。

成果物は実行ページの **Artifacts → voicebox-video-実行ID**。MP4・サムネイル・技術メトリクス・CPU音声の実行記録を7日保存します。台本・原素材・音声キャッシュ・認証はGitや共有キャッシュへ入れません。動画制作は手動起動。コードのpushでは短い結合テストだけを実行します。

## 制作入力ZIP

`production` の `project_zip_url` にHTTPSのZIP URLを指定します。認証情報を含む非公開URLはリポジトリのActions Secret `PROJECT_ZIP_URL`を使います。台本をコミットする必要はありません。

```text
project.json
runtime-assets.json
characters/
  Zundamon/  # 承認済み正規PNGパーツ
  Metan/
background.jpg
materials/   # 指定画像・SVG・動画・任意BGM
```

`runtime-assets.json` は `{"shell":"characters","background":"background.jpg"}`。原素材は利用者の提供ZIPで渡し、公開リポジトリへ再配布しません。フォントは公式Zen Maru Gothic Mediumをランナーが取得します。

`project.json` の基本契約は `title`、`visuals`、`lines`。各台詞は `id`・`speaker`（ずんだもん/四国めたん）・`text`・`emotion`・`visual` を持ちます。読みを変える場合だけ `speech_text` を指定します。表情は `NORMAL/HAPPY/SERIOUS/SURPRISED/TROUBLED/SKEPTICAL`。

## 編集機能

- **MoviePy 2.2.1**：MP4等をそのまま資料として指定可能。`edit: {"start":1,"end":4,"crop":[0,0,640,360]}` で切り抜き。複数動画は `clips: [{"file":"a.mp4","start":0,"end":3},{"file":"b.mp4","start":1,"end":4}]` と `transition:0.35` でクロスフェード。編集した動画は中央の資料領域で実際に再生し、終端では最終フレームを保持する。
- **CairoSVG 2.9.1**：`visuals[].file` に自己完結したSVGを指定すると高解像度PNGへ変換。外部URLやローカルファイルを参照するSVGは読み込まない。
- **FFmpeg**：`audio: {"bgm":"materials/music.wav","bgm_gain_db":-24,"ducking":true}` で、声が出る時にBGMを下げて合成。`normalize:true` を追加すると−17LUFSを狙う正規化を有効化する。BGMは提供素材だけを使い、自動取得しない。
- **効果音**：台詞に `se: {"file":"materials/pop.wav","offset":0,"gain_db":-18,"duration":0.4}` を指定。`offset` は実音声の台詞開始からの秒数。`audio.transition_se` に同じ形式を指定すると資料が切り替わる台詞の開始へ自動配置。`audio.se: {"pop":"materials/pop.wav"}` の登録があれば台詞を `se:"pop"` と短く書ける。SEは提供素材のみ。映像は再符号化せず、声・BGM・SEを音声側で合成し、配置記録を `sound-effects.json` に残す。
- **サムネイル**：完成映像の実フレームにタイトルを重ね、1280×720の `thumbnail.png` を出力。`thumbnail_title` で短いタイトルを指定できる。キャラクターの代替画像を生成しない。

画像・動画・BGMの相対パスは台本JSONのフォルダが基準。`visuals` の各項目に `id`、出典情報 `source_credit/source_url` を付けます。画像の代わりに `clips` を指定する場合は `file` を省略できます。

標準は1280×720・30fps、H.264 CRF20、AAC 48kHz mono。字幕はZen Maru Gothic Medium 36px・最大3行。声はVOICEVOX style3/2、原音話速1.10・後段1.15。字幕本文を保持し、長文は時間方向に分けます。静止画だけの既存台本も同じ入口で動きます。

## Google Driveへリポジトリを同期

GitHub Actionsから追跡対象ファイルのスナップショットをGoogle Driveへ送れます。初回設定と実行方法は[Google Drive同期手順](docs/GOOGLE_DRIVE_SYNC.md)を参照してください。

## VM・Linuxでも実行

既存VMでは `./zundamon-video setup` で保存済みエンジンと素材を登録し、`./zundamon-video run /path/project.json --out /path/new-output`。既存サービス・完成動画・別作業は上書きしません。

別のLinuxではPython 3.10以降・FFmpeg・Cairo・7zipを用意し、`python3 -m venv .venv`、`.venv/bin/pip install -r requirements.txt`。

```bash
.venv/bin/python tools/install_voicevox.py --directory "$HOME/.cache/voicebox-tools/voicevox-0.25.2"
./zundamon-video setup --engine-dir "$HOME/.cache/voicebox-tools/voicevox-0.25.2" \
  --shell /path/to/approved/characters --font /path/to/ZenMaruGothic-Medium.ttf \
  --background /path/to/blackboard.jpg
./zundamon-video run /path/project.json --out /path/new-output
```

10プロセスは10倍速を保証しません。実行記録の `max_parallel_synthesis`・起動時間・音声工程・描画時間を見ます。同じ音声と成功済み動画は再利用し、入力が変わった時は新しい出力先を指定します。

## 情報収集からJSON台本へ

[Actions → Research and JSON Project](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/workflows/prepare-project.yml) でテーマを入力。既定の `provider:none` はAPIキー不要でRSS・DuckDuckGo検索・本文抽出を行い、`research.json` と `script-prompt.json` をArtifactsへ残す。検索結果だけを正しい台本と見なしたり、台本未生成の状態を完成動画と表示したりしません。

GeminiまたはGroqで台本も生成する場合は、providerと具体的なmodelを指定し、選択したサービスのActions Secret `GEMINI_API_KEY` / `GROQ_API_KEY` を設定。1回の明示呼出しだけで、別モデルへのfallbackや自動再試行はありません。無料枠の適用はそのアカウント・モデルの条件によります。Tavily・Perplexityの有料APIは標準経路へ追加していません。

ローカルでは次の入口を使います。`request.json`・`script.json` は利用者の作業フォルダに置き、Gitへコミットしません。

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python tools/prepare_project.py --request /work/request.json --output /work/new-source-pack
# 提供台本を使う場合：追加API呼出しなし
.venv/bin/python tools/prepare_project.py --request /work/request.json --script /work/script.json --output /work/new-project
# 選択した台本APIを明示実行する場合だけ --allow-api を追加
```

リクエストは `{"topic":"解説するテーマ","queries":["検索語"],"urls":["https://example.org/article"],"feeds":[],"minutes":10,"max_sources":8,"provider":"none"}`。`news:false`・`search:false` で指定URL/RSSだけの収集にもできます。RSSはGoogle News検索RSSを既定とし、任意のRSS/Atomも指定可能。DDGSが検索、Trafilaturaが本文を抽出します。取得時刻・公開日・URL・本文SHA・失敗は素材パックに残します。本文未取得の記事は台本資料に使いません。

提供台本は既存の `title/lines` 形式に加え、画像のような配列形式も受け付けます。`speaker:zundamon/metan` と小文字の `emotion` を正規形式へ変換し、台詞本文は保持。各台詞の `source_id` を `research.json` の資料IDへ結びます。`search_keyword` は同じ検索語の取得済み記事へ対応付けできます。検索語だけで未取得の画面を作ることはありません。

台本がある場合、Playwright Chromiumが参照する記事だけを最大4並列で撮影。既存Cookieは使わず、広告や本文を勝手に書き換えません。本文領域を高さ700px以内で切り出し、URL・撮影時刻・SHA付きで `materials/manifest.json` へ記録。撮影失敗は記録して停止し、別画像へ置換しません。`capture_selector` で記事領域のCSSセレクターを指定できます。10サイトが何秒で完了するかは接続先次第です。

生成した `project.json` と `materials/` に正規立ち絵・背景・`runtime-assets.json` を合わせて入力ZIPにすれば、そのままproductionへ渡せます。また、production ZIPの `project.json` を `research-request.json` に置き換え、provider/modelを指定し、起動時の `allow_script_api` を有効にすると、記事収集→JSON台本→撮影→CPU音声→動画まで同じランナーで続行します。台本APIのSecretがない場合は自動生成できません。目標分数は台本作成の目安で、最終実尺は音声生成後のmetricsで示します。

`RESEARCH_READY` は情報収集済み、`PROJECT_READY` は台本と撮影素材の準備済み。MP4完成とは区別します。これらのAPIによる新規台本生成・任意ニュース全体の正確性まで、ローカルの結合テストが保証するものではありません。YouTubeへの自動投稿は含めません。

## YouTubeの公開動画を検索

[Actions → YouTube Search Research](https://github.com/nyu1791-collab/voiceBox-Pipeline/actions/workflows/youtube-research.yml) から手動実行できます。日本語の検索語と結果数（1〜5件）を入力すると、YouTube Data API v3で公開動画のタイトル・チャンネル・公開日・説明文を取得し、`youtube-search.json` をArtifactsへ7日間保存します。APIキーはリポジトリSecret `YOUTUBE_API_KEY` から読み、出力には含めません。`search.list` は1回100クォータ単位を消費するため、手動実行に限定し、定期実行や自動投稿は設定していません。

既存接続はGemini API（台本生成）、Google News RSS/DuckDuckGo（検索）、GitHub Actions、Google Drive同期（Workload Identity Federation）です。Google Custom Search APIなど新しい課金・権限が絡む接続は、使う機能が決まってから追加します。

必読は [docs/RULES.md](docs/RULES.md) だけ。ツールの由来と条件は [THIRD_PARTY.md](THIRD_PARTY.md)。過去のVM動作記録は [docs/RUN_RESULT.md](docs/RUN_RESULT.md) です。
