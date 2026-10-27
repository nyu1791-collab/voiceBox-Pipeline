"""Find related images and keep the page each image came from."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
import hashlib

from research import fetch, public_url


def collect_related_images(query, outdir, *, limit=8, search=None, fetcher=fetch):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    result = {'query': query, 'images': [], 'errors': []}
    try:
        if search is None:
            from ddgs import DDGS
            search = lambda q, count: DDGS(timeout=15).images(q, max_results=count)
        candidates = search(query, max(1, int(limit)))
    except Exception as error:
        result['errors'].append({'stage': 'image_search', 'error': type(error).__name__})
        return result

    def save(row):
        try:
            source = public_url(row.get('url', ''))
            image_url = public_url(row.get('image', ''))
            raw, _ = fetcher(image_url, maximum=8 * 1024**2)
            from PIL import Image, ImageOps
            with Image.open(BytesIO(raw)) as picture:
                picture = ImageOps.exif_transpose(picture)
                picture.thumbnail((1920, 1080))
                picture = picture.convert('RGBA' if 'A' in picture.getbands() else 'RGB')
                identity = hashlib.sha256((source + '\n' + image_url).encode()).hexdigest()[:16]
                filename = identity + '.png'
                picture.save(outdir / filename, format='PNG')
            return {'id': 'img-' + identity, 'file': 'materials/related/' + filename,
                    'source_url': source, 'source_credit': row.get('title') or urlsplit(source).hostname,
                    'source_image_url': image_url if not urlsplit(image_url).query else None,
                    'capture_kind': 'related_image', 'sha256': hashlib.sha256((outdir / filename).read_bytes()).hexdigest(),
                    'captured_utc': datetime.now(timezone.utc).isoformat(),
                    'search_engine': row.get('source', 'image search')}
        except Exception as error:
            return {'error': type(error).__name__}

    unique = {}
    for candidate in candidates:
        unique.setdefault((candidate.get('url'), candidate.get('image')), candidate)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(save, unique.values()))
    seen = set()
    for row in rows:
        if row.get('error'):
            result['errors'].append({'stage': 'image_fetch', 'error': row['error']})
        elif row['id'] not in seen:
            seen.add(row['id'])
            result['images'].append(row)
    return result
