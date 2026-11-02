"""YouTubeへ動画をアップロードする。既定は非公開（private）。公開は --privacy public を付けた時だけ。
認証は利用者が作ったOAuthクライアント（デスクトップアプリ）のJSONを使い、`auth` で一度だけ同意する。
保存する token JSON には refresh token が入るため、secrets/ に置いてGitへ入れない。値は表示しない。

使い方:
  python tools/youtube_upload.py auth --client-secret ..\\..\\secrets\\youtube_client.json --token ..\\..\\secrets\\youtube_token.json
  python tools/youtube_upload.py upload 動画.mp4 --title "タイトル" --description-file 概要欄.txt --token ..\\..\\secrets\\youtube_token.json
"""
import argparse
import http.server
import json
import secrets
import sys
import urllib.parse
from pathlib import Path

import requests

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_URI = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}/oauth2callback"
PRIVACY = ("private", "unlisted", "public")
CATEGORY_SCIENCE_TECHNOLOGY = "28"


def load_client(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    client = data.get("installed") or data.get("web")
    if not client or not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("OAuthクライアントのJSONが合いません（デスクトップアプリで作ったJSONを指定してください）")
    return client["client_id"], client["client_secret"]


def auth_url(client_id, state):
    query = {"client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": SCOPE,
             "access_type": "offline", "prompt": "consent", "state": state}
    return AUTH_URI + "?" + urllib.parse.urlencode(query)


def run_auth(client_secret, token_out, timeout=300):
    """ローカルの待受けで同意結果を受け取り、refresh token を保存する。"""
    client_id, client_secret_value = load_client(client_secret)
    state = secrets.token_urlsafe(24)
    result = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if urllib.parse.urlsplit(self.path).path != "/oauth2callback":
                self.send_response(404)
                self.end_headers()
                return
            result["state_ok"] = query.get("state", [""])[0] == state
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("認証を受け取りました。このタブは閉じて構いません。".encode("utf-8"))

        def log_message(self, *args):
            return  # 認証コードをログに出さない

    server = http.server.HTTPServer(("127.0.0.1", PORT), Callback)
    server.timeout = timeout
    print("次のURLをブラウザで開き、YouTubeのアカウントで許可してください:")
    print(auth_url(client_id, state))
    for _ in range(5):  # 別のパス（/favicon.ico など）の要求は読み飛ばす
        server.handle_request()
        if result:
            break
    server.server_close()
    if result.get("error") or not result.get("code"):
        raise RuntimeError("認証が完了しませんでした（許可されなかったか、時間切れです）")
    if not result.get("state_ok"):
        raise RuntimeError("認証の確認に失敗しました。もう一度 auth を実行してください")
    response = requests.post(TOKEN_URI, data={"code": result["code"], "client_id": client_id,
                                              "client_secret": client_secret_value, "redirect_uri": REDIRECT_URI,
                                              "grant_type": "authorization_code"}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"トークン交換に失敗しました（HTTP {response.status_code}）")
    tokens = response.json()
    if not tokens.get("refresh_token"):
        raise RuntimeError("refresh token が返りませんでした。Googleの権限画面で許可を取り直してください")
    out = Path(token_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret_value,
                               "refresh_token": tokens["refresh_token"]}, indent=2) + "\n", encoding="utf-8")
    print(f"認証OK。トークンを保存しました（値は表示しません）: {out}")


def access_token(token_path):
    token = json.loads(Path(token_path).read_text(encoding="utf-8"))
    response = requests.post(TOKEN_URI, data={"client_id": token["client_id"], "client_secret": token["client_secret"],
                                              "refresh_token": token["refresh_token"],
                                              "grant_type": "refresh_token"}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"アクセストークンの更新に失敗しました（HTTP {response.status_code}）。auth をやり直してください")
    return response.json()["access_token"]


def upload_video(video, title, description, token_path, privacy="private", category=CATEGORY_SCIENCE_TECHNOLOGY):
    if privacy not in PRIVACY:
        raise ValueError(f"privacy は {', '.join(PRIVACY)} のいずれかです")
    video = Path(video)
    if not video.is_file():
        raise FileNotFoundError(video)
    metadata = {"snippet": {"title": title[:100], "description": description[:5000], "categoryId": category,
                            "defaultLanguage": "ja", "defaultAudioLanguage": "ja"},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    headers = {"Authorization": f"Bearer {access_token(token_path)}"}
    start = requests.post(UPLOAD_URI, params={"uploadType": "resumable", "part": "snippet,status"}, headers={
        **headers, "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(video.stat().st_size)},
        json=metadata, timeout=60)
    if start.status_code != 200 or not start.headers.get("Location"):
        raise RuntimeError(f"アップロードを開始できませんでした（HTTP {start.status_code}）")
    with video.open("rb") as stream:
        done = requests.put(start.headers["Location"], data=stream, headers={"Content-Type": "video/mp4"}, timeout=3600)
    if done.status_code not in (200, 201):
        raise RuntimeError(f"アップロードに失敗しました（HTTP {done.status_code}）")
    video_id = done.json()["id"]
    return {"video_id": video_id, "url": f"https://youtu.be/{video_id}", "privacy": privacy}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="cmd", required=True)
    auth = commands.add_parser("auth", help="一度だけ許可してトークンを保存")
    auth.add_argument("--client-secret", required=True, type=Path)
    auth.add_argument("--token", required=True, type=Path)
    up = commands.add_parser("upload", help="非公開でアップロード（既定）")
    up.add_argument("video", type=Path)
    up.add_argument("--title", required=True)
    up.add_argument("--description-file", type=Path, default=None)
    up.add_argument("--token", required=True, type=Path)
    up.add_argument("--privacy", default="private", choices=PRIVACY, help="public は利用者が明示した時だけ")
    args = parser.parse_args()
    if args.cmd == "auth":
        run_auth(args.client_secret, args.token)
        return 0
    description = args.description_file.read_text(encoding="utf-8") if args.description_file else ""
    result = upload_video(args.video, args.title, description, args.token, privacy=args.privacy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
