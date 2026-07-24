'''Analyze a single page image and report in-page facts for extraction.

Usage: oe2d-analyze-page path/to/page.png
       oe2d-analyze-page path/to/source.pdf --page 3

Prints a JSON dict of per-page properties a downstream extractor can route on:
candidate orientation (columns vs rows), whether contest names / candidate names
/ headers are visible on THIS page, the precinct scope and axis, and the page's
skew in degrees. This is a single-image DSPy program (the composite PageAnalyzer),
distinct from the per-file source categorizer in `oe2d.categorize` (which reasons
over a whole file with tools) and from any inter-page table stitching, which
happens at a different level.

skew_degrees is the one non-VLM field: a VLM can't estimate fine rotation from an
image, so PageAnalyzer measures it deterministically with oe2d.pages.deskew on the
same image (also exposed on its own as oe2d-detect-skew).

A source that is not already an image is rendered to one first (a page of a PDF,
a sheet of a spreadsheet) via oe2d.categorize.rendering, so the same program
serves both raw page images and pages pulled from source files.

The program is a composite dspy.Module (PageAnalyzer): its forward() runs the VLM
content prediction AND measures skew deterministically in-module, so a page's full
per-page facts — the six VLM content fields plus skew_degrees — come back as one
prediction. Skew is not an LM output; it is computed by oe2d.pages.deskew on the
same image, folded into the program so there is no separate step to run.
'''
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys
import typing

import dotenv
import dspy
import pydantic
from PIL import Image

from .. import categorize
from . import deskew


# Extensions we treat as already-rendered page images; anything else is rendered.
_IMAGE_EXTS: tuple[str, ...] = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp')

# Render source pages at this DPI for inference. High enough that the dense
# candidate-column tables stay legible (220 undersamples them); the committed
# training images are rendered density-tiered (300 dense / 220 sparse), and 300
# here keeps the demanding case matched.
INFERENCE_DPI: int = 300


# Allowed per-page label vocabularies, as explicit Literal types; the DSPy output
# fields and the pydantic result model both take their types from these so the
# taxonomy has a single definition.
CandidateOrientation = typing.Literal['columns', 'rows']
PrecinctScope = typing.Literal['multi_precinct', 'per_precinct', 'county']
# The precinct axis is only meaningful for multi_precinct pages; 'none' covers
# per_precinct (one precinct, named in a header) and county (no precinct at all),
# so the field is always a concrete literal rather than null.
PrecinctAxis = typing.Literal['rows', 'columns', 'none']

CANDIDATE_ORIENTATIONS: tuple[str, ...] = typing.get_args(CandidateOrientation)
PRECINCT_SCOPES: tuple[str, ...] = typing.get_args(PrecinctScope)
PRECINCT_AXES: tuple[str, ...] = typing.get_args(PrecinctAxis)


class PageProperties(pydantic.BaseModel):
    '''In-page facts about a single rendered election-results page.

    The first six fields are VLM content predictions. skew_degrees is different:
    a VLM can't estimate fine (sub-2 deg) page rotation from an image — it defaults
    to ~0 regardless — so it is measured deterministically by oe2d.pages.deskew and
    folded in by the PageAnalyzer module. It is a program output but NOT a trained
    or scored field (see CONTENT_FIELDS / metrics.FIELD_WEIGHTS).
    '''
    candidate_orientation: CandidateOrientation
    contest_name_present: bool
    candidate_names_present: bool
    headers_present: bool
    precinct_scope: PrecinctScope
    precinct_orientation: PrecinctAxis
    # Detector-sourced (deskew), positive = counter-clockwise; NOT a VLM output.
    skew_degrees: float


# The VLM content fields — what the trained predictor emits and what datasets and
# metrics work over. Skew is excluded here on purpose (it is not learned).
CONTENT_FIELDS: tuple[str, ...] = (
    'candidate_orientation', 'contest_name_present', 'candidate_names_present',
    'headers_present', 'precinct_scope', 'precinct_orientation',
)

# Every field the composite program returns: the content fields plus the
# detector-sourced skew. Kept in the PageProperties order.
OUTPUT_FIELDS: tuple[str, ...] = CONTENT_FIELDS + ('skew_degrees',)


# The trained program, committed as package data. Loaded onto the predictor when
# present so an installed oe2d analyzes pages with the optimized prompt; absent,
# the stock prompt is used. optimize.py writes here.
OPTIMIZED_MODEL_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'model', 'optimized_page_analyzer.json')


class PageAnalysis(dspy.Signature):
    '''Report factual, in-page observations about ONE election-results page image.

    You are shown a single page image, not a whole document. Describe only what is
    visible on THIS page; do not infer contests or precincts that would be on other
    pages.
    '''
    image: dspy.Image = dspy.InputField(desc='A single rendered election-results page')
    candidate_orientation: CandidateOrientation = dspy.OutputField(
        desc="'columns' when each candidate/party is a column (and precincts run "
             "down the rows); 'rows' when each candidate/party is a row")
    contest_name_present: bool = dspy.OutputField(
        desc='Is a contest/office title visible on this page? A continuation page '
             'that just carries more candidate columns or more precinct rows often '
             'has none')
    candidate_names_present: bool = dspy.OutputField(
        desc='Are candidate or party names visible on this page? False on a bare '
             'data-only continuation page')
    headers_present: bool = dspy.OutputField(
        desc='Are column/row headers labeling the numbers present on this page?')
    precinct_scope: PrecinctScope = dspy.OutputField(
        desc="'multi_precinct' when the page lays out many precincts along an axis; "
             "'per_precinct' when the page is a single precinct named in a heading "
             "with its results below; 'county' when the page shows county-wide "
             "aggregates with no precinct dimension")
    precinct_orientation: PrecinctAxis = dspy.OutputField(
        desc="For a multi_precinct page, whether precincts are 'rows' or 'columns'; "
             "otherwise 'none'")


def _instrument() -> None:
    '''Turn on cmpnd tracing when a key is configured; otherwise do nothing.

    Mirrors oe2d.categorize._instrument but tags traces 'oe2d-pages'. Loads a
    repo-local .env explicitly rather than relying on litellm's import-time side
    effect, so the key source stays visible here.
    '''
    dotenv.load_dotenv()
    key: str | None = os.environ.get('CMPND_API_KEY')
    if not key:
        return
    try:
        import cmpnd
        cmpnd.configure(
            api_key=key,
            endpoint=os.environ.get('CMPND_ENDPOINT'),
            project_tags=['oe2d-pages'],
        )
        cmpnd.auto_instrument()
    except Exception:
        pass


def render_source(path: str, page: int = 1, member: str | None = None,
                  resolution: int = INFERENCE_DPI) -> str:
    '''Return a page image path: the file itself if already an image, else render.'''
    if os.path.splitext(path)[1].lower() in _IMAGE_EXTS:
        return path
    from ..categorize import rendering
    return rendering.render_page(path, page, member, resolution=resolution)


def _image_to_pil(image: dspy.Image) -> Image.Image:
    '''Recover a PIL image from a dspy.Image so skew can be measured on it.

    A dspy.Image built from a path stores its bytes as a base64 data URI in .url
    (verified format 'data:image/png;base64,<b64>'); decode that. A .url that is a
    plain filesystem path (no data: scheme) is opened directly.
    '''
    url: str = image.url
    if url.startswith('data:'):
        _, _, encoded = url.partition(',')
        return Image.open(io.BytesIO(base64.b64decode(encoded)))
    return Image.open(url)


class PageAnalyzer(dspy.Module):
    '''The full per-page program: VLM content prediction plus deterministic skew.

    forward() runs the trained content predictor and, on the same image, measures
    skew with the projection-profile detector, returning both as one prediction.
    Skew adds no LM overhead — it is a direct numeric computation, not a tool the
    LM decides to call.
    '''
    def __init__(self) -> None:
        super().__init__()
        self.analyze: dspy.Module = dspy.Predict(PageAnalysis)

    def forward(self, image: dspy.Image) -> dspy.Prediction:
        content = self.analyze(image=image)
        skew: float = deskew.detect_skew_pil(_image_to_pil(image))
        return dspy.Prediction(
            candidate_orientation=content.candidate_orientation,
            contest_name_present=content.contest_name_present,
            candidate_names_present=content.candidate_names_present,
            headers_present=content.headers_present,
            precinct_scope=content.precinct_scope,
            precinct_orientation=content.precinct_orientation,
            skew_degrees=skew,
        )


def build_analyzer() -> PageAnalyzer:
    '''Construct the composite page analyzer, loading the trained prompt if present.'''
    analyzer: PageAnalyzer = PageAnalyzer()
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        analyzer.load(OPTIMIZED_MODEL_PATH)
    return analyzer


def analyze_image(image_path: str) -> dict:
    '''Run the analyzer on an already-rendered page image; return a plain dict.'''
    _instrument()
    # The task LM is the shared Kimi K2 (multimodal) model defined once in
    # oe2d.categorize; this program reads only the page image with it.
    dspy.configure(lm=dspy.LM(categorize.TASK_LM))
    analyzer: PageAnalyzer = build_analyzer()
    prediction = analyzer(image=dspy.Image(image_path))
    properties = PageProperties(
        candidate_orientation=prediction.candidate_orientation,
        contest_name_present=prediction.contest_name_present,
        candidate_names_present=prediction.candidate_names_present,
        headers_present=prediction.headers_present,
        precinct_scope=prediction.precinct_scope,
        precinct_orientation=prediction.precinct_orientation,
        skew_degrees=float(prediction.skew_degrees),
    )
    return properties.model_dump()


def analyze_page(path: str, page: int = 1, member: str | None = None) -> dict:
    '''Analyze a page of a source file (rendering it first if it is not an image).'''
    return analyze_image(render_source(path, page, member))


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Analyze a single election-results page image.',
    )
    parser.add_argument('path', help='A page image, or a source file to render')
    parser.add_argument('--page', type=int, default=1,
                        help='1-based page/sheet to render when given a source file')
    parser.add_argument('--member', help='Zip member to render (for zip sources)')
    parser.add_argument('-v', '--verbose', action='store_true', help='log LM steps')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    print(json.dumps(analyze_page(args.path, args.page, args.member), indent=2))


if __name__ == '__main__':
    main()
