'''Vision sub-program: look at a rendered page and report facts as text.

This is the bridge across the RLM sandbox boundary. A host-side tool renders a
page to an image and calls PageInspector (a multimodal DSPy program); only the
returned facts string crosses into the Deno interpreter, never image bytes.
'''
from __future__ import annotations

from . import rendering

_program = None
_vision_lm = None

_DEFAULT_QUESTION = (
    'Describe this election-results page: are candidates in columns or rows, '
    'what is the geographic grain (precinct/district/county), and are there '
    'layout quirks (rotated headers, stacked or side-by-side contests, a '
    'table-of-contents/cover sheet, a scanned/bitmap page)?'
)


def configure(vision_lm) -> None:
    '''Set the multimodal LM the inspector runs on, and reset the cached program.'''
    global _vision_lm, _program
    _vision_lm = vision_lm
    _program = None


def _get_program():
    global _program
    if _program is None:
        import dspy

        class PageInspector(dspy.Signature):
            '''Report factual observations about an election-results page image.

            Say whether each candidate occupies a column or a row, the
            geographic grain, and any layout quirks. Report only what is
            visible; do not guess beyond the image.
            '''
            image: dspy.Image = dspy.InputField(desc='A rendered page from a source file')
            question: str = dspy.InputField(desc='What to look for')
            facts: str = dspy.OutputField(desc='Observed facts answering the question')

        program = dspy.Predict(PageInspector)
        if _vision_lm is not None:
            program.set_lm(_vision_lm)
        _program = program
    return _program


def inspect_page(path: str, page: int = 1, member: str | None = None, question: str = '') -> str:
    '''Render a page/sheet and return a vision model's factual description.'''
    import dspy

    png_path: str = rendering.render_page(path, page, member)
    prediction = _get_program()(
        image=dspy.Image(png_path),
        question=question or _DEFAULT_QUESTION,
    )
    return prediction.facts
