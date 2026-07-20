'''Vision sub-program: look at a rendered page and report facts as text.

This is the bridge across the RLM sandbox boundary. A host-side tool renders a
page to an image and calls PageInspector (a multimodal DSPy program); only the
returned facts string crosses into the Deno interpreter, never image bytes.
'''
from __future__ import annotations

import logging
import os

import dspy

from . import rendering

logger = logging.getLogger(__name__)

_program = None

_DEFAULT_QUESTION = (
    'Describe this election-results page: are candidates in columns or rows, '
    'what is the geographic grain (precinct/district/county), and are there '
    'layout properties (rotated headers, stacked or side-by-side contests, a '
    'table-of-contents/cover sheet, a scanned/bitmap page)?'
)


class PageInspector(dspy.Signature):
    '''Report factual observations about an election-results page image.

    Say whether each candidate occupies a column or a row, the geographic grain,
    and any layout properties. Report only what is visible; do not guess beyond the
    image.
    '''
    image: dspy.Image = dspy.InputField(desc='A rendered page from a source file')
    question: str = dspy.InputField(desc='What to look for')
    facts: str = dspy.OutputField(desc='Observed facts answering the question')


# Carries no LM of its own — runs on the ambient dspy.settings.lm (the task LM
# the RLM configures), so its vision call goes through the same instrumented path
# as the RLM's own calls rather than a program-local set_lm.
INSPECTOR = dspy.Predict(PageInspector)


def inspect_page(path: str, page: int = 1, member: str | None = None, question: str = '') -> str:
    '''Render a page/sheet and return a vision model's factual description.'''
    png_path: str = rendering.render_page(path, page, member)
    logger.info(
        'inspect_page: rendered %s page %s%s -> %s (%d bytes); running vision',
        os.path.basename(path), page,
        f' member={member!r}' if member else '',
        os.path.basename(png_path), os.path.getsize(png_path),
    )
    try:
        prediction = INSPECTOR(
            image=dspy.Image(png_path),
            question=question or _DEFAULT_QUESTION,
        )
    except Exception:
        logger.exception('inspect_page: vision call FAILED for %s', os.path.basename(png_path))
        raise
    facts: str = prediction.facts
    logger.info('inspect_page: vision facts: %s', ' '.join(facts.split())[:400])
    return facts
