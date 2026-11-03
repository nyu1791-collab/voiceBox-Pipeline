"""選んだ話題の根拠になるXの1投稿を、1枚のカード画像にする（feed-watch.yml から制作の前に呼ぶ）。

  python tools/post_card.py --job 依頼.json --items new-items.json --media 作業フォルダ

Xのページは撮らない（Xの決まりとrobots.txtに従う）。カードは、公式APIで取った本文と、
投稿についていた画像（動画なら代表画像）を並べて組み立てた画面。結果は作業フォルダの card.png。
カードは manifest.json に記録し、動画制作の後は discard で他の素材と一緒に破棄する。
"""
import argparse
import base64
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = 'voiceBox-post-card/1.0 (+https://github.com/nyu1791-collab/voiceBox-Pipeline)'
MAX_PICTURE_BYTES = 10 * 1024 * 1024
FONT = '"Noto Sans CJK JP", "Noto Sans JP", "Yu Gothic UI", "Meiryo", sans-serif'
MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}


def build_html(post, picture_url=None):
    """The card as one HTML page. Every text from the post is escaped."""
    text = html.escape(post.get('summary') or post.get('title') or '')
    author = html.escape('@' + post['url'].split('/')[3]) if post.get('url', '').count('/') >= 3 else ''
    when = html.escape(post.get('published', ''))
    link = html.escape(post.get('url', ''))
    picture = f'<img src="{picture_url}" alt="">' if picture_url else ''
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin: 0; width: 1280px; height: 720px; background: #f1f5f9; font-family: {FONT}; color: #0f172a; }}
.card {{ display: flex; width: 1040px; height: 600px; margin: 60px auto; background: #fff; border-radius: 24px;
        box-shadow: 0 20px 60px rgba(15, 23, 42, .18); overflow: hidden; }}
.media {{ flex: 0 0 480px; background: #e2e8f0; display: flex; align-items: center; justify-content: center; }}
.media img {{ max-width: 480px; max-height: 600px; object-fit: contain; }}
.body {{ flex: 1; padding: 40px 44px; display: flex; flex-direction: column; }}
.author {{ font-size: 30px; font-weight: 800; }}
.text {{ font-size: 30px; line-height: 1.55; margin-top: 24px; white-space: pre-wrap; word-break: break-all; }}
.meta {{ margin-top: auto; font-size: 20px; color: #64748b; }}
</style></head><body><div class="card"><div class="media">{picture}</div><div class="body">
<div class="author">{author}</div><p class="text">{text}</p>
<div class="meta">{when}<br>{link}</div></div></div></body></html>'''


def picture_data_url(path):
    suffix = Path(path).suffix.lower()
    data = base64.b64encode(Path(path).read_bytes()).decode('ascii')
    return f'data:{MIME.get(suffix, "image/jpeg")};base64,{data}'


def download_picture(url, out_dir, session=None):
    """Save the picture of the post (images, or the preview of a video). Returns the file path or None."""
    if not url.startswith('https://'):
        return None
    session = session or requests.Session()
    try:
        response = session.get(url, stream=True, timeout=30, headers={'User-Agent': UA})
    except requests.RequestException:
        return None
    with response:
        if response.status_code != 200 or not response.headers.get('content-type', '').startswith('image/'):
            return None
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_PICTURE_BYTES:
                return None
    suffix = '.' + response.headers.get('content-type', 'image/jpeg').split('/')[1].split(';')[0]
    suffix = suffix if suffix in MIME else '.jpg'
    path = Path(out_dir) / ('card-picture' + suffix)
    path.write_bytes(bytes(data))
    return path


def render(page_html, out_path):
    """Take the picture of the built card page (our own page, not X's)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 720})
            page.set_content(page_html, wait_until='load')
            page.screenshot(path=str(out_path))
        finally:
            browser.close()
    return out_path


def make_card(job, items, media_dir, session=None):
    """Build card.png for the chosen X post and record it in the manifest. Returns the path, or None."""
    x_urls = job.get('x_urls') or []
    post = next((it for it in items if x_urls and it.get('url') == x_urls[0]), None)
    if not post:
        return None
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    picture_url = None
    picture = None
    for media in post.get('media', []):
        source = media.get('url') if media.get('kind') == 'image' else media.get('preview')
        if source:
            picture = download_picture(source, media_dir, session=session)
            if picture:
                picture_url = picture_data_url(picture)
                break
    out = render(build_html(post, picture_url), media_dir / 'card.png')
    if picture:
        picture.unlink(missing_ok=True)  # the picture is inside the card now
    record_card(media_dir, post, out)
    return out


def record_card(media_dir, post, path):
    manifest_path = Path(media_dir) / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'files': []}
    import hashlib
    data = Path(path).read_bytes()
    manifest.setdefault('files', []).append({
        'file': Path(path).name, 'source_url': post.get('url'), 'media_url': None, 'kind': 'card',
        'content_type': 'image/png', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
        'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--job', required=True, type=Path)
    parser.add_argument('--items', required=True, type=Path)
    parser.add_argument('--media', required=True, type=Path)
    args = parser.parse_args()
    job = json.loads(args.job.read_text(encoding='utf-8'))
    items = json.loads(args.items.read_text(encoding='utf-8')).get('items', [])
    path = make_card(job, items, args.media)
    print(f'カードを作りました: {path}' if path else '作るXの投稿がありませんでした')
    return 0


if __name__ == '__main__':
    sys.exit(main())
