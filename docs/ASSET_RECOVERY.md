# 本番素材の復旧

2026-10-20、利用者のGitHub保存指示により、元の制作VMから承認済み素材と引き継ぎを回収した。

素材の保管先は非公開の [voicebox-production/20261020](https://github.com/nyu1791-collab/ai-army-private-backup-20261013/tree/assets/voicebox-production-20261020/voicebox-production/20261020)。アクセスには同リポジトリへの権限が必要。公開コードのcloneだけでは立ち絵・黒板は取得されない。公開Releaseや公開Actionsの成果物へ原素材を転載しない。

保管物は正規のずんだもん・四国めたんシェル、利用者の黒板、Zen Maru Gothic MediumとOFL、Carefreeと配布元の利用条件、VMの移行文・制作ルール・撮影及び再描画helper。`asset-manifest.json`にファイル単位の元パス・バイト数・SHA-256を保存し、`runtime-assets.json`は素材の相対パスを示す。認証情報やエンジン本体、過去の完成動画は含めない。

## 復旧

```bash
gh repo clone nyu1791-collab/ai-army-private-backup-20261013 /absolute/private-assets -- --branch assets/voicebox-production-20261020 --single-branch
```

1. `voicebox-production/20261020/asset-manifest.json`の各ファイルのサイズ・SHA-256を照合する。原素材の改行や文字コードを変更しない。
2. 公式VOICEVOX 0.25.2 CPU版と本リポジトリの依存を準備する。元VMでは既存エンジンとvenvを再利用できる。
3. `runtime-assets.json`の相対パスを保管ディレクトリから解決し、`pipeline.py setup --engine-dir ... --shell ... --background ... --font ...`で登録する。
4. `pipeline.py run /absolute/new/project.json --out /absolute/new/output`で制作する。新規の調査・台本・証拠画像は作品ごとの新しい入力先へ保存する。

VMルールは過去版として保存している。そこに記された30fps・GPU・Colab経路へ戻さず、現行の[ルール](RULES.md)の640×360・24fps・CPU10エンジン、ずんだもんが質問・四国めたんが解説、章とあらすじ表・実音声同期の図表/SEを使う。BGMの作者表示とリンク、立ち絵の権利表記を作品の概要欄へ保持する。
