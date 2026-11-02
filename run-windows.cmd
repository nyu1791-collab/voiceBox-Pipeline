@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Prepare the repository's .venv first. See docs/WINDOWS_LOCAL.md.
  pause
  exit /b 1
)
if not defined VOICEBOX_CAIRO_BIN if exist "..\..\tools\cairo\mingw64\bin\libcairo-2.dll" set "VOICEBOX_CAIRO_BIN=%~dp0..\..\tools\cairo\mingw64\bin"
if defined VOICEBOX_CAIRO_BIN set "PATH=%VOICEBOX_CAIRO_BIN%;%PATH%"
if defined VOICEBOX_CAIRO_BIN set "CAIROCFFI_DLL_DIRECTORIES=%VOICEBOX_CAIRO_BIN%"
if "%~1"=="" (
  .venv\Scripts\python.exe pipeline.py --help
  pause
) else (
  .venv\Scripts\python.exe pipeline.py %*
)
exit /b %errorlevel%
