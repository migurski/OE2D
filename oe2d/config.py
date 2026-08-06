'''Shared cross-module configuration: the single on-disk cache home and source-cache naming.

Kept as its own tiny top-level module so contests and votes both use one cache root without importing
each other. Everything cacheable -- downloaded source PDFs, Textract results, the DSPy LM cache -- lives
under CACHE_ROOT, so the caches sit and clear together and a remote run can mount ONE volume for all of
them. OE2D_CACHE_DIR overrides the location (e.g. a mounted volume path); the default is oe2d-cache/ in
the cwd (the repo root in normal use). The whole tree is gitignored.
'''
from __future__ import annotations

import hashlib
import os
import re
import urllib.parse

CACHE_ROOT: str = os.environ.get('OE2D_CACHE_DIR') or os.path.join(os.getcwd(), 'oe2d-cache')
# Downloaded source documents (contests + votes share this, keyed by url, so an overlapping document
# -- Alameda, Humboldt -- downloads once for both).
SOURCE_CACHE_DIR: str = os.path.join(CACHE_ROOT, 'sources')


def source_cache_name(url: str) -> str:
    '''A cache filename for a source url: a readable slug of the original filename PLUS a short hash of
    the full url, keeping the extension -- so a glance at the cache shows what is being worked on, while
    the hash keeps same-named files from different repos or pinned versions distinct. e.g.
    "2024-yolo-county-ca-precinct-level-results-a1b2c3d4e5f6.pdf".'''
    basename: str = urllib.parse.unquote(url.rsplit('/', 1)[-1])
    stem, ext = os.path.splitext(basename)
    slug: str = re.sub(r'[^a-z0-9]+', '-', stem.lower()).strip('-')[:60]
    digest: str = hashlib.sha1(url.encode()).hexdigest()[:12]
    return '%s-%s%s' % (slug, digest, ext.lower())
