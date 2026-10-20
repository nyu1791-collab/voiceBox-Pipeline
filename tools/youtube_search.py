"""Run a small, manual YouTube Data API v3 video search."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def search_videos(query, max_results=5, *, api_key=None):
    query = query.strip() if isinstance(query, str) else ''
    if not query or len(query) > 200:
        raise ValueError('query must contain 1 to 200 characters')
    if type(max_results) is not int or not 1 <= max_results <= 5:
        raise ValueError('max_results must be an integer between 1 and 5')
    api_key = api_key or os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        raise ValueError('YOUTUBE_API_KEY is not configured')

    params = urlencode({
        'part': 'snippet', 'type': 'video', 'q': query,
        'maxResults': max_results, 'regionCode': 'JP',
        'relevanceLanguage': 'ja', 'safeSearch': 'moderate',
        'fields': 'items(id/videoId,snippet(title,channelTitle,channelId,publishedAt,description,thumbnails/default/url))',
    })
    request = Request(
        'https://www.googleapis.com/youtube/v3/search?' + params,
        headers={'x-goog-api-key': api_key, 'Accept': 'application/json'},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as error:
        # HTTP client exceptions may contain request URLs or headers. Never print them.
        status = getattr(error, 'code', None)
        suffix = f' (HTTP {status})' if status else ''
        raise RuntimeError('YouTube Data API request failed' + suffix) from None

    videos = []
    for item in payload.get('items', []):
        video_id = item.get('id', {}).get('videoId')
        snippet = item.get('snippet', {})
        if not video_id:
            continue
        videos.append({
            'video_id': video_id,
            'url': f'https://www.youtube.com/watch?v={video_id}',
            'title': snippet.get('title', ''),
            'channel_title': snippet.get('channelTitle', ''),
            'channel_id': snippet.get('channelId', ''),
            'published_at': snippet.get('publishedAt', ''),
            'description': snippet.get('description', '')[:5000],
            'thumbnail_url': snippet.get('thumbnails', {}).get('default', {}).get('url', ''),
        })
    return {
        'query': query,
        'provider': 'YouTube Data API v3',
        'retrieved_utc': datetime.now(timezone.utc).isoformat(),
        'result_count': len(videos),
        'videos': videos,
    }


def main():
    if len(sys.argv) != 4:
        raise SystemExit('usage: python tools/youtube_search.py QUERY MAX_RESULTS OUTPUT_JSON')
    query, max_results, output = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    result = search_videos(query, max_results)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Saved {result['result_count']} YouTube results to {output}")


if __name__ == '__main__':
    main()
