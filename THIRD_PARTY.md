# 利用する既存ツールと素材

- [VOICEVOX Engine](https://github.com/VOICEVOX/voicevox_engine)：既存0.25.2 CPU版。エンジンと音声ライブラリの利用条件は公式配布物を参照。`tools/install_voicevox.py`が公式CPUリリースを取得し、GitHub公開SHA-256で照合する。エンジン本体をGitへ保存・再配布しない。
- [FFmpeg](https://github.com/FFmpeg/FFmpeg)：映像符号化・音声速度調整・音声結合。VMまたはGitHubランナーのコマンドを使う。BGMダッキングと任意の音量正規化も担当する。
- [Pillow](https://github.com/python-pillow/Pillow)：既存描画器の画像合成。
- [Zen Maru Gothic](https://github.com/googlefonts/zen-marugothic)：VMのMediumフォント、または公式Google Fontsの固定コミット・SHAに一致するフォントを使う。OFLの配布物を参照し、Gitへ同梱しない。
- 立ち絵：坂本アヒル氏の正規素材をNightWork氏のシェルパーツから既存描画器で合成する。[ずんだもん](https://seiga.nicovideo.jp/seiga/im10788496)、[四国めたん](https://seiga.nicovideo.jp/seiga/im10791276)、[シェル配布元](https://nightwork.sakura.ne.jp/)。承認済みVM素材を参照し、原素材は同梱しない。
- 黒板：利用者が提供したVMの既存画像を参照する。同梱しない。
- `renderer/` は既存VMの描画コードを移植したもの。由来・元のSHAは `source-provenance.json`。外部工程の古い補助関数も互換のため保持しているが、この制作入口は台本作成・素材検索・外部モーション・GPU・モデル呼出しへ分岐しない。
- `examples/*.png` はこのリポジトリの `tools/make_example.py` が描く説明図。外部の画面・報道写真・実測結果ではない。

- [MoviePy](https://github.com/Zulko/moviepy)：2.2.1、MIT。動画の切り抜き・クロップ・クロスフェード・動画資料のフレーム取得。
- [CairoSVG](https://github.com/Kozea/CairoSVG)：2.9.1、LGPL-3.0。提供SVGを高解像度PNGへ変換。
- [Remotion](https://github.com/remotion-dev/remotion)も調査したが、利用条件が異なるため今回の標準依存には追加しない。
- Actionsのdemoは一時領域で組み立てた短い音声・動く図形・テスト用合成音による機能実行。正規立ち絵を使うproductionと区別する。
