"""Deterministic checks run while a project is prepared: image rights and numeric grounding.

Nothing here calls a model or waits for a person. Each finding is recorded in
preparation.json so the rendered video's inputs can be audited afterwards.
"""
import re
from urllib.parse import urlsplit

# Frames of other people's videos and playback-blocked pages are never shown.
BLOCKED_CAPTURE_KINDS = {'youtube_frame', 'youtube_metadata', 'page_playback_blocked'}
PUBLIC_SUFFIXES = ('.go.jp', '.lg.jp')
PUBLIC_HOST = re.compile(r'(^|\.)(pref|city|town|village|vill|police)[a-z0-9.-]*\.jp$')
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
# Counts of the same kind of thing must agree across sources (e.g. 113種類 vs 110項目).
COUNT_UNITS = re.compile(r'(\d[\d,]*)\s*(種類|項目)')


def image_allowed(visual):
    """Return (allowed, reason) for one visual before it can appear in the video."""
    kind = visual.get('capture_kind', '')
    if kind in BLOCKED_CAPTURE_KINDS:
        return False, 'third-party video or playback capture'
    if kind == 'related_image':
        host = (urlsplit(visual.get('source_url', '')).hostname or '').lower()
        if host.endswith(PUBLIC_SUFFIXES) or PUBLIC_HOST.search(host):
            return True, 'public-sector page; keep source credit'
        return False, 'related image from a non-public host'
    return True, 'source page capture; keep source credit'


def numbers_in(text):
    return {match.group(0).replace(',', '') for match in NUMBER.finditer(text or '')}


def numeric_checks(lines, sources):
    """Return (numbers the script uses that no source states, conflicting counts across sources)."""
    corpus = '\n'.join(f"{source.get('title', '')}\n{source.get('text', '')}" for source in sources)
    stated = numbers_in(corpus)
    unsupported = [{'line_id': line['id'], 'number': number}
                   for line in lines for number in sorted(numbers_in(line.get('text', '')))
                   if number not in stated]
    values = {}
    for match in COUNT_UNITS.finditer(corpus):
        values.setdefault('反則行為の数', set()).add(match.group(1).replace(',', ''))
    conflicts = [{'unit': unit, 'values': sorted(found)}
                 for unit, found in values.items() if len(found) > 1]
    return unsupported, conflicts
