"""Upload an SRT/VTT caption track to an existing YouTube video (captions.insert).

captions.insert costs 400 quota units per call (default daily quota 10,000).
Nothing is sent unless ``--confirm`` is given; the default prints the plan.

OAuth (scope https://www.googleapis.com/auth/youtube.force-ssl) is read from
the environment and never printed:
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
"""
from pathlib import Path
import argparse
import json
import os
import re
import sys
import uuid

QUOTA_UNITS = 400
TOKEN_URL = 'https://oauth2.googleapis.com/token'
UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/captions?part=snippet&uploadType=multipart'
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
CONTENT_TYPES = {'.srt': 'application/x-subrip', '.vtt': 'text/vtt'}


def build_request(video_id, caption_file, language='ja', name='日本語', draft=False):
    """Return (metadata, body_bytes, content_type) for a multipart upload."""
    if not VIDEO_ID.match(str(video_id)):
        raise ValueError('video id must be the 11-character YouTube id')
    path = Path(caption_file)
    if path.suffix.lower() not in CONTENT_TYPES:
        raise ValueError('caption file must be .srt or .vtt')
    data = path.read_bytes()
    if not data.strip():
        raise ValueError('caption file is empty')
    if len(data) > 100 * 1024 * 1024:
        raise ValueError('caption file exceeds 100MB')
    metadata = {'snippet': {'videoId': video_id, 'language': language, 'name': name, 'isDraft': bool(draft)}}
    boundary = 'caption-' + uuid.uuid4().hex
    body = b''.join([
        f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode(),
        json.dumps(metadata, ensure_ascii=False).encode(), b'\r\n',
        f'--{boundary}\r\nContent-Type: {CONTENT_TYPES[path.suffix.lower()]}\r\n\r\n'.encode(),
        data, f'\r\n--{boundary}--\r\n'.encode(),
    ])
    return metadata, body, f'multipart/related; boundary={boundary}'


def access_token():
    import requests
    names = ('YOUTUBE_CLIENT_ID', 'YOUTUBE_CLIENT_SECRET', 'YOUTUBE_REFRESH_TOKEN')
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise ValueError('Missing OAuth environment: ' + ', '.join(missing))
    response = requests.post(TOKEN_URL, data={
        'client_id': os.environ['YOUTUBE_CLIENT_ID'], 'client_secret': os.environ['YOUTUBE_CLIENT_SECRET'],
        'refresh_token': os.environ['YOUTUBE_REFRESH_TOKEN'], 'grant_type': 'refresh_token'}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f'OAuth refresh failed: HTTP {response.status_code}')
    return response.json()['access_token']


def upload(video_id, caption_file, language='ja', name='日本語', draft=False):
    import requests
    _, body, content_type = build_request(video_id, caption_file, language, name, draft)
    response = requests.post(UPLOAD_URL, data=body, timeout=120,
                             headers={'Authorization': 'Bearer ' + access_token(), 'Content-Type': content_type})
    if response.status_code not in (200, 201):
        reason = ''
        try:
            reason = response.json()['error']['errors'][0]['reason']
        except (ValueError, KeyError, IndexError):
            pass
        raise RuntimeError(f'captions.insert failed: HTTP {response.status_code} {reason}'.strip())
    result = response.json()
    return {'caption_id': result.get('id'), 'video_id': video_id, 'language': language, 'quota_units': QUOTA_UNITS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=f'YouTube に字幕を追加（1回 {QUOTA_UNITS} ユニット）')
    parser.add_argument('video_id')
    parser.add_argument('caption_file', type=Path)
    parser.add_argument('--language', default='ja')
    parser.add_argument('--name', default='日本語')
    parser.add_argument('--draft', action='store_true', help='下書きとして追加（視聴者に非表示）')
    parser.add_argument('--confirm', action='store_true', help='実際に送信する（省略時は内容確認だけ）')
    args = parser.parse_args(argv)
    metadata, body, _ = build_request(args.video_id, args.caption_file, args.language, args.name, args.draft)
    if not args.confirm:
        print(json.dumps({'status': 'DRY_RUN', 'quota_units_if_sent': QUOTA_UNITS, 'bytes': len(body),
                          **metadata['snippet']}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({'status': 'UPLOADED', **upload(args.video_id, args.caption_file, args.language, args.name, args.draft)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f'STOP: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
