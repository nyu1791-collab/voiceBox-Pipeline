"""Read public source pages through Firecrawl before Gemini writes the script."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
import socket
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser

AGENT = 'voiceBox-Pipeline/1.0'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def checked_public_url(url):
    parts = urlsplit(url)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
            or parts.password or parts.fragment or any(
                any(word in name.lower() for word in ('key', 'token', 'secret', 'password', 'auth', 'signature'))
                for name, _ in parse_qsl(parts.query, keep_blank_values=True))):
        raise ValueError('Unsafe URL')
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80),
                                   type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Non-public host')
    return parts


def scrape(url):
    parts = checked_public_url(url)
    opener = build_opener(NoRedirect)
    headers = {'User-Agent': AGENT}
    try:
        with opener.open(Request(f'{parts.scheme}://{parts.netloc}/robots.txt', headers=headers), timeout=15) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError('robots.txt too large')
        robots = RobotFileParser()
        robots.parse(content.decode('utf-8', errors='replace').splitlines())
        if not robots.can_fetch(AGENT, url):
            raise ValueError('robots.txt disallows access')
    except HTTPError as error:
        if error.code != 404:
            raise
    # A redirect or an access denial is not retried via the external service.
    with opener.open(Request(url, headers=headers), timeout=20) as response:
        if not 200 <= response.status < 300:
            raise ValueError('Page unavailable')
    payload = json.dumps({'url': url, 'formats': ['markdown'], 'onlyMainContent': True,
                          'proxy': 'basic', 'storeInCache': False}).encode()
    api_headers = {'Content-Type': 'application/json'}
    # An installed key alone must not unexpectedly incur charges.
    key = os.environ.get('FIRECRAWL_API_KEY', '').strip() if os.environ.get('VOICEBOX_FIRECRAWL_USE_KEY') == '1' else ''
    if os.environ.get('VOICEBOX_FIRECRAWL_USE_KEY') == '1' and not key:
        raise ValueError('Firecrawl API key is not configured')
    if key:
        api_headers['Authorization'] = 'Bearer ' + key
    request = Request('https://api.firecrawl.dev/v2/scrape', data=payload, headers=api_headers, method='POST')
    with urlopen(request, timeout=60) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Firecrawl response too large')
    result = json.loads(raw)
    data = result.get('data') or {}
    metadata = data.get('metadata') or {}
    status = metadata.get('statusCode')
    text = (data.get('markdown') or '').strip()
    if not result.get('success') or not isinstance(status, int) or not 200 <= status < 300 or not text:
        raise ValueError('Firecrawl did not return a readable page')
    final_url = metadata.get('url') or url
    if final_url != url:
        raise ValueError('Unexpected redirect')
    return {'text': text[:18000], 'title': metadata.get('title'), 'final_url': final_url,
            'body_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def enrich_sources(sources, limit=8):
    """Keep Gemini summaries when extraction fails; record exactly what happened."""
    attempted = 0
    for source in sources:
        if source.get('is_youtube'):
            source['firecrawl_status'] = 'SKIPPED_VIDEO'
            continue
        if attempted >= limit:
            source['firecrawl_status'] = 'SKIPPED_LIMIT'
            continue
        attempted += 1
        try:
            data = scrape(source['url'])
            if not data.get('title'):
                data.pop('title', None)
            source.update(data, extraction_status='EXTRACTED', firecrawl_status='EXTRACTED',
                          retrieved_utc=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            # Exception text can contain a URL or a credential: do not persist it.
            source['firecrawl_status'] = 'FAILED'
            source['firecrawl_error'] = type(error).__name__
    return sources
