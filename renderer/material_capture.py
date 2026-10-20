"""Capture once per URL, then crop the smallest relevant text block."""
import argparse
import asyncio
import datetime
import hashlib
import json
import time
from pathlib import Path

from pipeline_common import atomic_json

# Wait before each retry after HTTP 429 (rate limit). Tests override this.
RETRY_DELAYS = (60, 180)
MAX_RETRY_AFTER = 300
NAV_SELECTOR = 'nav, #toc, .vector-toc, .infobox, .navbox'
BLOCK_XPATH = ('xpath=ancestor-or-self::*[self::p or self::h1 or self::h2 or self::h3 '
               'or self::h4 or self::li or self::tr][1]')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def pick_block(page, text, used, timeout_ms=4000):
    """Return (locator, key) of the first visible match outside navigation whose block is not used yet.

    The first DOM match can be hidden (a collapsed table of contents), and two targets can
    point at the same paragraph. Callers add `key` to `used` only after the screenshot succeeds.
    """
    matches = page.get_by_text(text, exact=False)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for index in range(await matches.count()):
            candidate = matches.nth(index)
            if not await candidate.is_visible():
                continue
            if await candidate.evaluate("e => Boolean(e.closest('%s'))" % NAV_SELECTOR):
                continue
            block = candidate.locator(BLOCK_XPATH)
            selected = block if await block.count() else candidate
            key = hashlib.sha256((await selected.inner_text()).encode()).hexdigest()
            if key not in used:
                return selected, key
        if time.monotonic() >= deadline:
            raise TimeoutError('no unused visible match: ' + text)
        await page.wait_for_timeout(250)


def retry_wait(response, default):
    value = response.headers.get('retry-after', '')
    return min(int(value), MAX_RETRY_AFTER) if value.isdigit() else default


async def goto_with_retry(page, url, retries):
    """Load a page. On HTTP 429 wait and retry a bounded number of times; record each wait."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response is None or response.status != 429 or attempt == len(RETRY_DELAYS):
            return response
        wait = retry_wait(response, RETRY_DELAYS[attempt])
        retries.append({'status': 429, 'wait_seconds': wait})
        await asyncio.sleep(wait)
    return response


async def capture_targets(targets, outdir, playwright_factory=None):
    if playwright_factory is None:
        from playwright.async_api import async_playwright
        playwright_factory = async_playwright
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    grouped = {}
    for target in targets:
        grouped.setdefault(target['url'], []).append(target)
    manifest = []
    async with playwright_factory() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/google-chrome',
                                          headless=True, args=['--no-sandbox'])
        semaphore = asyncio.Semaphore(3)

        async def grab(url, rows):
            async with semaphore:
                page = await browser.new_page(viewport={'width': 1280, 'height': 900},
                                              device_scale_factor=1)
                retries = []
                entries = [{'id': t['id'], 'url': url, 'captured_utc': None, 'files': [],
                            'crops': [], 'retries': retries} for t in rows]
                used = set()
                try:
                    response = await goto_with_retry(page, url, retries)
                    if response is None or response.status >= 400:
                        raise ValueError('HTTP ' + str(response.status if response else 'no response'))
                    # Font readiness is bounded; analytics must not hold networkidle open.
                    await page.wait_for_function('document.fonts.status === "loaded"', timeout=3000)
                    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
                    full = outdir/(hashlib.sha256(url.encode()).hexdigest()[:16]+'_full.png')
                    await page.screenshot(path=str(full), full_page=True, timeout=12000)
                    for target, entry in zip(rows, entries):
                        entry.update(captured_utc=stamp, title=await page.title(),
                                     files=[{'path': full.name, 'sha256': digest(full)}])
                        for crop in target.get('crops', []):
                            result = {'name': crop['name'], 'text': crop['text'], 'found': False}
                            try:
                                selected, key = await pick_block(page, crop['text'], used)
                                await selected.scroll_into_view_if_needed(timeout=4000)
                                dst = outdir/(target['id']+'_'+crop['name']+'.png')
                                await selected.screenshot(path=str(dst), timeout=6000)
                                from PIL import Image
                                with Image.open(dst) as original:
                                    if original.height > 700:
                                        raise ValueError('paragraph crop too tall')
                                used.add(key)
                                result.update(found=True, file=dst.name, sha256=digest(dst))
                            except Exception as exc:
                                result['error'] = type(exc).__name__ + ': ' + str(exc)[:120]
                            entry['crops'].append(result)
                except Exception as exc:
                    for entry in entries:
                        entry['error'] = type(exc).__name__ + ': ' + str(exc)[:160]
                finally:
                    await page.close()
                    manifest.extend(entries)
        try:
            await asyncio.gather(*(grab(url, rows) for url, rows in grouped.items()))
        finally:
            await browser.close()
    manifest.sort(key=lambda x: x['id'])
    atomic_json(outdir/'manifest.json', manifest)
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('targets', type=Path)
    ap.add_argument('output', type=Path)
    a = ap.parse_args()
    if (a.output/'manifest.json').exists():
        raise ValueError('既存撮影を上書きしない。素材準備入口で再利用する')
    rows = asyncio.run(capture_targets(json.loads(a.targets.read_text()), a.output))
    failures = [r['id'] for r in rows if r.get('error') or any(not c.get('found') for c in r['crops'])]
    print(json.dumps({'pages': len(rows), 'unique_urls': len({r['url'] for r in rows}),
                      'failures': failures}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
