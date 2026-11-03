"""RSSと RSSHub の経路を見て、前回から増えた記事だけを書き出す（feed-watch.yml から8時間ごとに呼ぶ）。
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
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
UA = 'voiceBox-feed-watch/1.0 (+https://github.com/nyu1791-collab/voiceBox-Pipeline)'
KEEP_DAYS = 21
SUMMARY_LIMIT = 400
OUT_LIMIT = 1200  # 105の情報源を8時間ごとに見る


def item_id(link, guid=''):
    return hashlib.sha1((guid or link).encode('utf-8')).hexdigest()[:16]


def plain(text, limit=SUMMARY_LIMIT):
    return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', text or '')).split())[:limit]


def fetch(url):
    response = requests.get(url, headers={'User-Agent': UA}, timeout=30)
    response.raise_for_status()  # a 404 page must be reported, not read as an empty feed
    return response.content


def read_rss(feed, limit, get=fetch):
    parsed = feedparser.parse(get(feed['url']))
    # Newest first, whatever order the feed uses; entries without a date keep their place at the end.
    entries = sorted(parsed.entries, key=lambda e: tuple(e.get('published_parsed') or e.get('updated_parsed') or ()),
                     reverse=True)
    items = []
    for entry in entries[:limit]:
        link = entry.get('link', '')
        if not link.startswith('https://') and not link.startswith('http://'):
            continue
        items.append({'feed': feed['id'], 'category': feed.get('category', ''),
                      'title': plain(entry.get('title', ''), 200), 'url': link,
                      'published': entry.get('published', entry.get('updated', '')),
                      'summary': plain(entry.get('summary', '')), 'id': item_id(link, entry.get('id', ''))})
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
        items[feed['id']] = [{'feed': feed['id'], 'category': feed.get('category', ''),
                              'title': plain(it.get('title', ''), 200), 'url': it.get('link', ''),
                              'published': str(it.get('pubDate', '')), 'summary': plain(it.get('description', '')),
                              'id': item_id(it.get('link', ''), str(it.get('guid', '')))}
                             for it in (result.get('item') or [])[:limit] if it.get('link')]
    return items, errors


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
    items, errors = [], {}
    for feed in config['feeds']:
        if feed['type'] != 'rss':
            continue
        try:
            items += read_rss(feed, limit)
        except Exception as exc:  # one broken feed must not stop the others
            errors[feed['id']] = f'{type(exc).__name__}: {exc}'[:300]
    hub_items, hub_errors = read_rsshub([f for f in config['feeds'] if f['type'] == 'rsshub'], limit, args.rsshub_dir)
    for feed_items in hub_items.values():
        items += feed_items
    errors.update(hub_errors)

    now = datetime.now(timezone.utc)
    state = load_state(args.state)
    fresh, first_run = select_new(items, state, now)
    save_state(args.state, state)
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
