# 利用する既存ツールと素材

- [VOICEVOX Engine](https://github.com/VOICEVOX/voicevox_engine)：既存0.25.2 CPU版。エンジンと音声ライブラリの利用条件は公式配布物を参照。`tools/install_voicevox.py`が公式CPUリリースを取得し、GitHub公開SHA-256で照合する。エンジン本体をGitへ保存・再配布しない。
- [FFmpeg](https://github.com/FFmpeg/FFmpeg)：映像符号化・音声速度調整・音声結合。VMまたはGitHubランナーのコマンドを使う。BGMダッキングと任意の音量正規化も担当する。
- [Pillow](https://github.com/python-pillow/Pillow)：既存描画器の画像合成。
- [Zen Maru Gothic](https://github.com/googlefonts/zen-marugothic)：VMのMediumフォント、または公式Google Fontsの固定コミット・SHAに一致するフォントを使う。OFLの配布物を参照し、Gitへ同梱しない。
- 立ち絵：坂本アヒル氏の正規素材をNightWork氏のシェルパーツから既存描画器で合成する。[ずんだもん](https://seiga.nicovideo.jp/seiga/im10788496)、[四国めたん](https://seiga.nicovideo.jp/seiga/im10791276)、[シェル配布元](https://nightwork.sakura.ne.jp/)。承認済みVM素材を参照し、原素材は同梱しない。
- 黒板：利用者が提供したVMの既存画像を参照する。同梱しない。
- `renderer/` は既存VMの描画コードを移植したもの。由来・元のSHAは `source-provenance.json`。任意の台本準備・検索は `tools/prepare_project.py` が担当し、GPUや外部モーションへ自動分岐しない。
- `examples/*.png` はこのリポジトリの `tools/make_example.py` が描く説明図。外部の画面・報道写真・実測結果ではない。

- [MoviePy](https://github.com/Zulko/moviepy)：2.2.1、MIT。動画の切り抜き・クロップ・クロスフェード・動画資料のフレーム取得。
- [CairoSVG](https://github.com/Kozea/CairoSVG)：2.9.1、LGPL-3.0。提供SVGを高解像度PNGへ変換。
- [Remotion](https://github.com/remotion-dev/remotion)も調査したが、利用条件が異なるため今回の標準依存には追加しない。
- Actionsのdemoは一時領域で組み立てた短い音声・動く図形・テスト用合成音による機能実行。正規立ち絵を使うproductionと区別する。
- [DDGS](https://github.com/deedy5/ddgs)：9.16.0、MIT。DuckDuckGoバックエンドで検索。提供先の接続制限に従い、無制限再試行はしない。
- [feedparser](https://github.com/kurtmckee/feedparser)：6.0.14、BSD。RSS/Atomを読み取る。PyGoogleNewsの代わりにフィードを直接取得する。
- [Trafilatura](https://github.com/adbar/trafilatura)：2.3.1、Apache-2.0。記事本文とメタデータの抽出。抽出結果を自動的な事実確認と扱わない。
- [Playwright](https://github.com/microsoft/playwright-python)：1.64.0、Apache-2.0。ヘッドレスChromiumで参照先を並列撮影。既存の `renderer/material_capture.py` を再利用し、Chrome固定パスを取り除いた。
- 任意のGemini/Groq呼出しは各サービスのAPI条件に従う。無料枠・速度・Actions全体の無期限無料を保証しない。新規契約・キー作成・自動課金切替は実装しない。

## 今回比較した追加ツール

| GitHub | 採用判断・接続先 |
|---|---|
| [Google BudouX](https://github.com/google/budoux) | **採用**。0.9.0、Apache-2.0。CPUの同梱モデルで日本語文節を分け、`subtitle_tools.py`から映像字幕の折返し・時間分割へ接続。外部APIやモデルの追加取得なし。Python 3.10以降を維持するため、3.11以上が必要な0.10.0は使わない。 |
| [pysubs2](https://github.com/tkarabela/pysubs2) | **採用**。1.8.1、MIT。実音声区間と映像の字幕状態を編集用SRT/ASS/VTTへ出力。Python 3.12以上が必要な1.9.0を避け、Actionsの3.11とローカル3.10以降に合わせる。 |
| [ffmpeg-normalize](https://github.com/slhck/ffmpeg-normalize) | 調査済み・標準依存へ追加しない。ライセンス本文はMIT相当。現在のFFmpegに音量調整とloudnormがあり、同じ処理の重複導入を避ける。2パス正規化が必要な場合の候補。 |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 調査済み・標準依存へ追加しない。BSD-3-Clause。提供映像の場面検出には有用だが、OpenCVなどの追加依存が必要。クリップの自動場面分割が必要な時に選択する。 |

「採用」は制作コードへ接続したもの。「調査済み」は導入・実行済みを意味しない。追加ツールを理由にGPUや有料生成へ移行しない。
