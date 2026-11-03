# X の公開投稿を、ログインしていないブラウザ（プライベートウィンドウ）で開き、本文の部分を画像に切り抜く。
# 使い方（PowerShell）:
#   .\tools\rumor_video\capture_post.ps1 -Url https://x.com/OpenAI/status/2114780711664398349 -Name OpenAI-2114780711664398349.png
# 保存先: media\input\x-screenshots\（Gitには入れない）。取得の記録は manifest.json に追記する。
# 注意: ログイン、Cookie、APIキーは使わない。画面は本文の部分だけを切り抜く。
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Brave = 'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
    [string]$OutDir = 'C:\Users\nyu17\yu-lab\media\input\x-screenshots'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class CapWin {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$scratch = Join-Path $env:TEMP 'x-capture'
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

Start-Process -FilePath $Brave -ArgumentList '--incognito', $Url
Start-Sleep -Seconds 7
$p = Get-Process brave -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($p) { [CapWin]::ShowWindow($p.MainWindowHandle, 3) | Out-Null; [CapWin]::SetForegroundWindow($p.MainWindowHandle) | Out-Null }
Start-Sleep -Seconds 1
[System.Windows.Forms.SendKeys]::SendWait('{HOME}')
Start-Sleep -Seconds 2

$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$full = Join-Path $scratch ('full-' + $Name)
$bmp.Save($full, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

# 投稿の本文の部分（画面の左上の基準位置から切り抜く。画面サイズが違う場合は位置を調整する）
$img = [System.Drawing.Bitmap]::FromFile($full)
$crop = $img.Clone((New-Object System.Drawing.Rectangle 405, 178, 875, 542), $img.PixelFormat)
$out = Join-Path $OutDir $Name
$crop.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$crop.Dispose(); $img.Dispose()

$entry = [ordered]@{ file = $Name; source_url = $Url; captured_utc = (Get-Date).ToUniversalTime().ToString('o'); method = 'ログインなしのブラウザで表示し、本文の部分を切り抜き' }
$manifest = Join-Path $OutDir 'capture-log.json'
$list = @()
if (Test-Path -LiteralPath $manifest) { $list = @(Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json) }
$list += [pscustomobject]$entry
$list | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifest -Encoding UTF8
Write-Output "saved: $out"
