"""RSS・RSSHub の経路・X（公式APIの自分の投稿）を見て、前回から増えた記事だけを書き出す。
feed-watch.yml から8時間ごとに呼ぶ。
既読の記録（state）が無い最初の実行では、今ある記事を既読にするだけで新着として出さない。
  python tools/feed_watch.py --config config/feeds.json --state feed-state.json --out new-items.json
RSSHub の経路は Node の rsshub パッケージで取得する（--rsshub-dir にインストール先を渡す）。費用はかからない。"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
UA = 'voiceBox-feed-watch/1.0 (+https://github.com/nyu1791-collab/voiceBox-Pipeline)'
KEEP_DAYS = 21
SUMMARY_LIMIT = 400
OUT_LIMIT = 1200  # 1回の実行で判断に渡す新着の上限（超えた分はログに出す）


def item_id(link, guid=''):
    return hashlib.sha1((guid or link).encode('utf-8')).hexdigest()[:16]


def plain(text, limit=SUMMARY_LIMIT):
    return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', text or '')).split())[:limit]


def fetch(url):
    response = requests.get(url, headers={'User-Agent': UA}, timeout=30)
    response.raise_for_status()  # a 404 page must be reported, not read as an empty feed
    return response.content


MEDIA_PER_ITEM = 4
IMG_SRC = re.compile(r'<img[^>]+src="(https://[^"]+)"', re.IGNORECASE)
MEDIA_EXT = {'.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.gif': 'image', '.webp': 'image',
             '.mp4': 'video', '.webm': 'video'}


def media_kind(content_type, url):
    """'image' or 'video' from the MIME type, or from the file extension when the type is missing."""
    if content_type.startswith('image/'):
        return 'image'
    if content_type.startswith('video/'):
        return 'video'
    suffix = Path(urlsplit(url).path).suffix.lower()
    return MEDIA_EXT.get(suffix)


def media_of(entry_or_item, html=''):
    """Image and video URLs (https only) that a feed item points to. They are downloaded later, only for
    the sources of a chosen topic (tools/feed_media.py)."""
    found = []

    def add(url, kind):
        if (kind and isinstance(url, str) and url.startswith('https://') and len(found) < MEDIA_PER_ITEM
                and url not in [m['url'] for m in found]):
            found.append({'url': url, 'kind': kind})

    for enclosure in entry_or_item.get('enclosures', []) or []:
        add(enclosure.get('href'), media_kind(enclosure.get('type', ''), enclosure.get('href', '')))
    for content in entry_or_item.get('media_content', []) or []:
        add(content.get('url'), media_kind(content.get('type', ''), content.get('url', '')))
    for thumbnail in entry_or_item.get('media_thumbnail', []) or []:
        add(thumbnail.get('url'), 'image')
    for url in IMG_SRC.findall(html):
        add(unescape(url), 'image')
    return found


def read_rss(feed, limit, get=fetch):
    parsed = feedparser.parse(get(feed['url']))
    source = plain(parsed.feed.get('title', ''), 80) or feed['id']  # 出典の名前（媒体名）
    # Newest first, whatever order the feed uses; entries without a date keep their place at the end.
    entries = sorted(parsed.entries, key=lambda e: tuple(e.get('published_parsed') or e.get('updated_parsed') or ()),
                     reverse=True)
    items = []
    for entry in entries[:limit]:
        link = entry.get('link', '')
        if not link.startswith('https://') and not link.startswith('http://'):
            continue
        html = (entry.get('summary') or '') + ' '.join(c.get('value', '') for c in entry.get('content', []) or [])
        items.append({'feed': feed['id'], 'category': feed.get('category', ''), 'source': source,
                      'title': plain(entry.get('title', ''), 200), 'url': link,
                      'published': entry.get('published', entry.get('updated', '')),
                      'summary': plain(entry.get('summary', '')), 'id': item_id(link, entry.get('id', '')),
                      'media': media_of(entry, html)})
    return items


RSSHUB_SCRIPT = r"""
import { init, request } from 'rsshub';
await init({ CACHE_TYPE: 'memory', NO_RANDOM_UA: 'true', LOGGER_LEVEL: 'error' });
const out = {};
for (const route of JSON.parse(process.argv[2])) {
  try { out[route] = await request(route); } catch (e) { out[route] = { error: { message: String(e?.message || e) } }; }
}
process.stdout.write('\n' + MARK + JSON.stringify(out));
process.exit(0);
""".replace('MARK', "'@@FEED_WATCH_JSON@@'")
MARK = '@@FEED_WATCH_JSON@@'  # RSSHub writes its request log to stdout too


def read_rsshub(feeds, limit, rsshub_dir):
    """Fetch every RSSHub route in one Node process. Returns {feed id: [items]} and {feed id: error}."""
    if not feeds:
        return {}, {}
    if not rsshub_dir:
        return {}, {f['id']: 'RSSHub is not installed (--rsshub-dir)' for f in feeds}
    script = Path(rsshub_dir) / 'feed_watch_rsshub.mjs'
    routes = [f['route'] for f in feeds]
    try:
        script.write_text(RSSHUB_SCRIPT, encoding='utf-8')
        done = subprocess.run(['node', str(script), json.dumps(routes)], cwd=rsshub_dir, capture_output=True,
                              text=True, encoding='utf-8', timeout=600)
        stdout, stderr = done.stdout or '', done.stderr or ''
        data = json.loads(stdout.split(MARK, 1)[1]) if MARK in stdout else {}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:  # RSS feeds still go on without RSSHub
        data, stderr = {}, f'{type(exc).__name__}: {exc}'
    items, errors = {}, {}
    for feed in feeds:
        result = data.get(feed['route']) or {'error': {'message': (stderr or 'no output')[-300:]}}
        if result.get('error'):
            errors[feed['id']] = result['error'].get('message', '')[:300]
            continue
        items[feed['id']] = [{'feed': feed['id'], 'category': feed.get('category', ''), 'source': feed['id'],
                              'title': plain(it.get('title', ''), 200), 'url': it.get('link', ''),
                              'published': str(it.get('pubDate', '')), 'summary': plain(it.get('description', '')),
                              'id': item_id(it.get('link', ''), str(it.get('guid', ''))),
                              'media': media_of({'enclosures': [{'href': it['enclosure']['url'],
                                                                  'type': it['enclosure'].get('type', '')}]}
                                                if (it.get('enclosure') or {}).get('url') else {},
                                                it.get('description', '') or '')}
                             for it in (result.get('item') or [])[:limit] if it.get('link')]
    return items, errors


X_API = 'https://api.x.com/2'
X_OTHER_USERS_TYPES = ('quoted', 'replied_to', 'retweeted')  # 他の人の文章を含む投稿の種類
X_USER = re.compile(r'^[A-Za-z0-9_]{1,15}$')


def x_http(url, token):
    """GET from the official X API with the bearer token. Errors show the status code only."""
    response = requests.get(url, headers={'Authorization': 'Bearer ' + token, 'User-Agent': UA}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f'X API HTTP {response.status_code}')
    return response.json()


def x_media(media):
    """One photo or video of an X post as {url, kind}. Videos take the highest-bitrate MP4."""
    kind = media.get('type')
    if kind == 'photo' and media.get('url', '').startswith('https://'):
        return {'url': media['url'], 'kind': 'image'}
    if kind in ('video', 'animated_gif'):
        mp4 = [v for v in media.get('variants', []) if v.get('content_type') == 'video/mp4' and
               str(v.get('url', '')).startswith('https://')]
        if mp4:
            best = max(mp4, key=lambda v: v.get('bit_rate') or 0)
            preview = media.get('preview_image_url', '')
            return {'url': best['url'], 'kind': 'video', 'preview': preview if preview.startswith('https://') else ''}
    preview = media.get('preview_image_url', '')
    return {'url': preview, 'kind': 'image'} if preview.startswith('https://') else None


def read_x_api(feed, limit, token, state, get=x_http):
    """Latest posts of one X account through the official API. Only posts newer than the last one read are
    fetched (since_id), so a post is read and charged once. Only the account's own posts are kept: retweets,
    replies and quote posts (which carry other users' text) are left out."""
    user = feed['user'].lstrip('@')
    if not X_USER.match(user):
        raise ValueError(f'invalid X user name: {user}')
    user_ids = state.setdefault('x_user_ids', {})
    if user not in user_ids:
        user_ids[user] = get(f'{X_API}/users/by/username/{user}', token)['data']['id']
    params = {'max_results': max(5, min(limit, 100)), 'exclude': 'retweets,replies',
              'tweet.fields': 'created_at,referenced_tweets,author_id',
              'expansions': 'attachments.media_keys', 'media.fields': 'type,url,preview_image_url,variants'}
    since = state.setdefault('x_since_id', {}).get(user)
    if since:
        params['since_id'] = since
    data = get(f'{X_API}/users/{user_ids[user]}/tweets?' + urlencode(params), token)
    newest = (data.get('meta') or {}).get('newest_id')
    if newest:
        state['x_since_id'][user] = newest
    media_by_key = {m.get('media_key'): m for m in (data.get('includes') or {}).get('media', [])}
    items = []
    for post in data.get('data', []):
        # 公式アカウント自身の投稿だけを読む。引用・返信・リポストは、ほかの人の文章を含むので除く。
        if any(ref.get('type') in X_OTHER_USERS_TYPES for ref in post.get('referenced_tweets') or []):
            continue
        if post.get('author_id') and post['author_id'] != user_ids[user]:
            continue
        media = [x_media(media_by_key[key]) for key in (post.get('attachments') or {}).get('media_keys', [])
                 if key in media_by_key]
        url = f'https://x.com/{user}/status/{post["id"]}'
        items.append({'feed': feed['id'], 'category': feed.get('category', ''), 'source': f'@{user}（X公式）',
                      'title': plain(post.get('text', ''), 200),
                      'url': url, 'published': post.get('created_at', ''), 'summary': plain(post.get('text', '')),
                      'id': item_id(url), 'media': [m for m in media if m][:MEDIA_PER_ITEM]})
    return items


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(path, state):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    tmp.replace(path)


def select_new(items, state, now):
    """Mark items as seen and return those not seen before. The first run only seeds the state."""
    seen = state.setdefault('seen', {})
    first_run = not seen
    stamp = now.isoformat(timespec='seconds')
    fresh = []
    for item in items:
        is_new = item['id'] not in seen
        seen[item['id']] = stamp  # refresh, so items that stay in a slow feed are not forgotten and re-sent
        if is_new and not first_run:
            fresh.append(item)
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat(timespec='seconds')
    state['seen'] = {key: value for key, value in seen.items() if value >= cutoff}
    return fresh, first_run


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=ROOT / 'config' / 'feeds.json')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--rsshub-dir', default=os.environ.get('RSSHUB_DIR'))
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding='utf-8'))
    limit = int(config.get('max_items_per_feed', 20))
    now = datetime.now(timezone.utc)
    state = load_state(args.state)
    items, errors = [], {}
    rss_feeds = [f for f in config['feeds'] if f['type'] == 'rss']
    # RSS の情報源は同時に取る（順番に待つと時間がかかるため）。失敗した情報源は記録し、ほかは続ける。
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {feed['id']: pool.submit(read_rss, feed, limit) for feed in rss_feeds}
        for feed_id, future in futures.items():
            try:
                items += future.result()
            except Exception as exc:
                errors[feed_id] = f'{type(exc).__name__}: {exc}'[:300]
    hub_items, hub_errors = read_rsshub([f for f in config['feeds'] if f['type'] == 'rsshub'], limit, args.rsshub_dir)
    for feed_items in hub_items.values():
        items += feed_items
    errors.update(hub_errors)
    token = os.environ.get('X_API_BEARER_TOKEN', '')
    for feed in [f for f in config['feeds'] if f['type'] == 'x_api']:
        if not token:
            errors[feed['id']] = 'X_API_BEARER_TOKEN is not configured'
            continue
        try:
            items += read_x_api(feed, limit, token, state)
        except Exception as exc:
            errors[feed['id']] = f'{type(exc).__name__}: {exc}'[:300]

    fresh, first_run = select_new(items, state, now)
    save_state(args.state, state)
    if len(fresh) > OUT_LIMIT:
        print(f'新着 {len(fresh)} 件のうち {len(fresh) - OUT_LIMIT} 件は、上限のため判断に渡さなかった')
    fresh = fresh[:OUT_LIMIT]
    args.out.write_text(json.dumps({'checked_at': now.isoformat(timespec='seconds'), 'items': fresh,
                                    'errors': errors}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for feed_id, message in errors.items():
        print(f'取得できなかった情報源: {feed_id}: {message}')
    print(f'記事 {len(items)} 件を確認、新着 {len(fresh)} 件' + ('（初回のため既読にしただけ）' if first_run else ''))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'new_count={len(fresh)}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
