"""選んだ話題の根拠記事だけ、RSSが指す画像・動画をダウンロードする。動画制作の後で破棄する。

  python tools/feed_media.py fetch --job 依頼.json --items new-items.json --out 作業フォルダ
  python tools/feed_media.py discard --out 作業フォルダ        （制作が終わったら実行。記録を残して削除）

ダウンロードは根拠記事（依頼の sources）に限り、https のみ、画像と動画だけを受け付ける。
上限は1話題あたり8ファイル・1ファイル30MB・合計150MB。GitHub Actions では Artifact に入れない
（このリポジトリは公開なので、Artifact は誰でも取れてしまうため）。
取った素材は制作での利用前に quality_gates.image_allowed を通す（権利の確認）。
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = 'voiceBox-feed-media/1.0 (+https://github.com/nyu1791-collab/voiceBox-Pipeline)'
MAX_FILES = 8
MAX_BYTES = 30 * 1024 * 1024
MAX_TOTAL = 150 * 1024 * 1024
EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp',
       'video/mp4': '.mp4', 'video/webm': '.webm'}
USAGE = ('参考用。制作で使う前に quality_gates.image_allowed で権利を確認する。'
         '動画制作の後は discard で破棄する。')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_limited(response, limit):
    """Read the body, stopping as soon as it passes the byte limit. Returns None when it is too large."""
    data = bytearray()
    for chunk in response.iter_content(65536):
        data.extend(chunk)
        if len(data) > limit:
            return None
    return bytes(data)


def fetch(job, items, out_dir, session=None):
    """Download the media of the job's source articles. Returns the manifest (also written to out_dir)."""
    session = session or requests.Session()
    sources = set(job.get('urls', []))
    articles = [it for it in items if it.get('url') in sources]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files, skipped, total = [], [], 0
    for article in articles:
        for media in article.get('media', []):
            url = media.get('url', '')
            if len(files) >= MAX_FILES:
                skipped.append({'url': url, 'reason': 'file limit'})
                continue
            if not url.startswith('https://'):
                skipped.append({'url': url, 'reason': 'not https'})
                continue
            try:
                response = session.get(url, stream=True, timeout=30, headers={'User-Agent': UA})
            except requests.RequestException as error:
                skipped.append({'url': url, 'reason': type(error).__name__})
                continue
            with response:
                if response.status_code != 200:
                    skipped.append({'url': url, 'reason': f'HTTP {response.status_code}'})
                    continue
                content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                if content_type not in EXT or content_type.split('/')[0] != media.get('kind'):
                    skipped.append({'url': url, 'reason': f'type {content_type or "missing"}'})
                    continue
                declared = int(response.headers.get('content-length') or 0)
                if declared > MAX_BYTES:
                    skipped.append({'url': url, 'reason': 'too large'})
                    continue
                data = read_limited(response, MAX_BYTES)
            if data is None:
                skipped.append({'url': url, 'reason': 'too large'})
                continue
            if total + len(data) > MAX_TOTAL:
                skipped.append({'url': url, 'reason': 'total limit'})
                continue
            digest = hashlib.sha256(data).hexdigest()
            name = f'{len(files) + 1:02d}-{digest[:12]}{EXT[content_type]}'
            (out_dir / name).write_bytes(data)
            total += len(data)
            files.append({'file': name, 'source_url': article['url'], 'media_url': url,
                          'kind': media['kind'], 'content_type': content_type, 'bytes': len(data),
                          'sha256': digest, 'retrieved_at': utc_now()})
    manifest = {'job_id': job.get('id'), 'topic': job.get('topic'), 'created': utc_now(), 'usage': USAGE,
                'files': files, 'skipped': skipped, 'total_bytes': total}
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                           encoding='utf-8')
    return manifest


def discard(out_dir):
    """Delete the downloaded media after the video is made. A receipt with the hashes is kept beside it."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return None
    manifest_path = out_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    receipt = {'job_id': manifest.get('job_id'), 'discarded_at': utc_now(),
               'files': [{'file': f['file'], 'sha256': f['sha256'], 'bytes': f['bytes']}
                         for f in manifest.get('files', [])]}
    receipt_path = out_dir.with_name(out_dir.name + '.discarded.json')
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shutil.rmtree(out_dir)
    return receipt_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('fetch')
    get.add_argument('--job', required=True, type=Path)
    get.add_argument('--items', required=True, type=Path, help='tools/feed_watch.py の出力（new-items.json）')
    get.add_argument('--out', required=True, type=Path)
    gone = commands.add_parser('discard')
    gone.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'fetch':
        job = json.loads(args.job.read_text(encoding='utf-8'))
        items = json.loads(args.items.read_text(encoding='utf-8')).get('items', [])
        manifest = fetch(job, items, args.out)
        print(f"画像・動画を {len(manifest['files'])} 件保存（{manifest['total_bytes']} バイト）、"
              f"見送り {len(manifest['skipped'])} 件")
    else:
        receipt = discard(args.out)
        print('破棄しました' + (f'（記録: {receipt}）' if receipt else '（対象がありません）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
