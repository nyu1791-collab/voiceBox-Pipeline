# Colab GPU経路（実機確認前）

この経路は実装途中。Colab上での実行・音声合成・書き出し・成果物回収は未確認であり、完成済みとは扱わない。

- `tools/install_voicevox_gpu.py` は公式0.25.2のLinux NVIDIA版2分割ファイルをSHA-256照合して展開する。
- `VOICEBOX_DEVICE=cuda` はGPUエンジン1台を `--use_gpu` で起動し、音声キャッシュをCPUと分離する。
- `tools/colab_gpu_run.py` はColab環境、NVIDIA GPU、実際のNVENC符号化を確かめてから動画制作を行う。素材・出力・エンジンの場所を引数で指定する。
- レイアウト描画はCPU、音声合成はVOICEVOX GPU、最終動画の符号化はNVENC。文字起こしは別のGemini API。
- 必要な作業: 認証済みColabランタイムへの接続、秘密を含まないコードと利用許諾済み素材の非公開受け渡し、GPU版エンジン・依存の実機互換確認、結果の非公開回収、失敗時の後始末。実機検証前に通常制作で使わない。
- 動画・台本・素材・認証情報を公開Gitや公開Actionsの成果物へ置かない。
