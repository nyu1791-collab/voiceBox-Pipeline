# 利用する既存ツールと素材

- [VOICEVOX Engine](https://github.com/VOICEVOX/voicevox_engine)：既存0.25.2 CPU版。エンジンと音声ライブラリの利用条件は公式配布物を参照。ダウンロード・再配布はこのリポジトリでは行わない。
- [FFmpeg](https://github.com/FFmpeg/FFmpeg)：映像符号化・音声速度調整・音声結合。VMのインストール済みコマンドを使用する。
- [Pillow](https://github.com/python-pillow/Pillow)：既存描画器の画像合成。
- [Zen Maru Gothic](https://github.com/googlefonts/zen-marugothic)：VMにあるMediumフォントを参照し、フォント自体は同梱しない。
- 立ち絵：坂本アヒル氏の正規素材をNightWork氏のシェルパーツから既存描画器で合成する。[ずんだもん](https://seiga.nicovideo.jp/seiga/im10788496)、[四国めたん](https://seiga.nicovideo.jp/seiga/im10791276)、[シェル配布元](https://nightwork.sakura.ne.jp/)。承認済みVM素材を参照し、原素材は同梱しない。
- 黒板：利用者が提供したVMの既存画像を参照する。同梱しない。
- `renderer/` は既存VMの描画コードを移植したもの。由来・元のSHAは `source-provenance.json`。外部工程の古い補助関数も互換のため保持しているが、この制作入口は台本作成・素材検索・外部モーション・GPU・モデル呼出しへ分岐しない。
- `examples/*.png` はこのリポジトリの `tools/make_example.py` が描く説明図。外部の画面・報道写真・実測結果ではない。
