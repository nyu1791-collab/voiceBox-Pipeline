from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image

from image_search import collect_related_images


def test_image_search_saves_image_and_source_page():
    data = BytesIO()
    Image.new('RGB', (100, 60), 'blue').save(data, format='PNG')
    source = 'https://example.com/article'
    image_url = 'https://example.com/photo.png'

    def search(query, count):
        return [{'title': '写真の出典', 'url': source, 'image': image_url, 'source': 'fixture'}]

    def fetch(url, *, maximum):
        assert url == image_url
        return data.getvalue(), url

    with tempfile.TemporaryDirectory() as directory:
        outdir = Path(directory)/'materials/related'
        result = collect_related_images('テーマ', outdir, search=search, fetcher=fetch)
        item = result['images'][0]
        assert item['source_url'] == source
        assert item['source_image_url'] == image_url
        assert item['source_credit'] == '写真の出典'
        assert (Path(directory) / item['file']).is_file()
