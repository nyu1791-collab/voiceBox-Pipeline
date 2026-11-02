# このPCでの準備と実行範囲

2026-11-01。Windows 11・Python 3.11の専用 `.venv` を使用する。全工程の制作は引き続きREADMEに記載したActions経路。Windowsで10エンジンを起動できるようにしたという意味ではない。`engine_pool.py` はLinux用の実行ファイル `run` と共有ライブラリを前提にしている。

`run-windows.cmd` をダブルクリックすると使い方を表示する。引数を渡す場合は、このフォルダでPowerShellを開き ` .\run-windows.cmd --help` を実行する。

SVG変換にはPythonのCairoSVGに加え、Cairo本体が必要。このPCでは `yu-lab/tools/cairo/mingw64/bin` にMSYS2のCairo 1.18.6と実行時DLLを配置した。パッケージのSHA256をMSYS2の配布一覧と照合し、ライセンスも保存した。MSYS2全体や別のPythonは導入していない。

起動用cmdがそのプロセスだけのPATHにDLLの場所を追加する。別の場所へ配置した場合は `VOICEBOX_CAIRO_BIN` でDLLのフォルダを指定する。PC全体のPATH・レジストリ・他のリポジトリのPythonには変更を加えない。

PowerShellから直接SVG関連の確認をする場合：

```powershell
$env:Path = 'C:\Users\nyu17\yu-lab\tools\cairo\mingw64\bin;' + $env:Path
.\.venv\Scripts\python.exe -m pytest -q tests/test_media_editing.py
```

制作素材は `C:\Users\nyu17\yu-lab\media\projects\voicebox-20261020\20261020` に移行済み。`runtime-assets.json` の相対パスをこのフォルダ基準で読む。過去のルールや完成記録は履歴であり、現行 `docs/RULES.md` に優先しない。

DLLの扱いは [CairoCFFI公式説明](https://doc.courtbouillon.org/cairocffi/stable/overview.html)、配布元は [MSYS2 Cairo](https://packages.msys2.org/packages/mingw-w64-x86_64-cairo)。
