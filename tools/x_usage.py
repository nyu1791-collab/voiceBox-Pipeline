"""X API の使用量を確かめる（手動実行。読み取りの件数と上限を表示する。鍵は表示しない）。

  X_API_BEARER_TOKEN を環境変数で渡して実行する（GitHub の x-usage.yml から呼ぶ）。
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = 'https://api.x.com/2/usage/tweets?days=7&usage.fields=daily_project_usage,daily_client_app_usage,cap_reset_day,project_cap'


def main():
    token = os.environ.get('X_API_BEARER_TOKEN', '')
    if not token:
        print('X_API_BEARER_TOKEN が設定されていません')
        return 1
    request = urllib.request.Request(URL, headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'voiceBox-x-usage/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        print(f'X API の使用量を取れませんでした（HTTP {error.code}）')
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
