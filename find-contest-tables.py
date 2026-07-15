'''Find contest tables with bounding box regions in election source PDFs.

Usage: find-contest-tables.py <filename> [--context "..."]

Uses a DSPy ReAct agent to explore PDF pages with flexible table extraction
strategies, returning contest names, page numbers, and bounding box regions
compatible with pdfplumber crop() and within_bbox().
'''
from __future__ import annotations

import os
import pprint
import typing
import pdfplumber
import pydantic
import source_table


class ContestTable(pydantic.BaseModel):
    contest_name: typing.Literal['President', 'U.S. Senate', 'U.S. House'] = pydantic.Field(description='Contest name')
    district_number: str | None = pydantic.Field(description='District number (for legislative races)')
    page_number: int = pydantic.Field(description='One-based page number')
    bbox: source_table.BBox = pydantic.Field(description='Bounding box region containing this contest table, compatible with pdfplumber crop() and within_bbox()')
    strategy: str = pydantic.Field(description='Extraction strategy that found this table: "lines", "lines_strict", or "text"')
    orientation: typing.Literal['candidate columns', 'candidate rows'] = pydantic.Field(description='Whether table rows or table columns represent candidates')


def main() -> None:
    import argparse

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Find contest tables with bounding boxes in a source file.',
    )
    parser.add_argument('filename', help='Path to the source file')
    parser.add_argument('--context', default='', help='Text about expected races and candidates')
    args: argparse.Namespace = parser.parse_args()

    import dspy, cmpnd

    class ContestTableFinder(dspy.Signature):
        """Identify all contest tables in the PDF file, with their bounding box regions.

        Use page_count to determine the number of pages. Use page_tables with different
        strategies ("lines", "lines_strict", "text") to find tables and their bounding
        boxes. Use page_words to scan for contest names and candidate names on each page.

        Not all pages have contests we care about. Each contest table should include the
        bounding box (x0, top, x1, bottom) from the page_tables result. When a single page
        has multiple contests side-by-side or stacked, report each one separately with its
        own bounding box. Contest names in tables may not exactly match our expected output.
        Table data is viewed in row-major order; note orientation in output to describe
        whether table rows represent candidates or table columns represent candidates.

        Context describes the expected races and candidates for this election. Use it to
        identify contests by recognizing candidate names even when contest titles are
        ambiguous or missing.
        """
        context: str = dspy.InputField(desc='Text describing expected races and candidates for this election')
        table_file_path: str = dspy.InputField()
        contest_tables: list[ContestTable] = dspy.OutputField()

    sonnet45 = dspy.LM("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    maverick17b = dspy.LM("bedrock/us.meta.llama4-maverick-17b-instruct-v1:0")
    dspy.configure(lm=maverick17b)
    detailer = dspy.RLM(
        ContestTableFinder,
        tools=[source_table.page_count, source_table.page_tables, source_table.page_words],
        verbose=True,
    )

    # Configure cmpnd tracing if API key is available
    cmpnd_key = os.environ.get("CMPND_API_KEY")
    if cmpnd_key:
        cmpnd.configure(api_key=cmpnd_key, project_tags=["ballotpedia-contest-tables"])
        cmpnd.auto_instrument()

    result = detailer(context=args.context, table_file_path=args.filename)
    pprint.pprint(result.contest_tables)

    pdf = pdfplumber.open(args.filename)
    
    for i, contest_table in enumerate(result.contest_tables):
        page = pdf.pages[contest_table.page_number - 1]
        img = page.to_image(resolution=200)
        bbox = {k: getattr(contest_table.bbox, k) for k in ('x0', 'top', 'x1', 'bottom')}
        img.draw_rect(bbox, fill=(0, 0x66, 0xff, 0x33))
        img.save(f'/tmp/out-{i} {contest_table.contest_name}.png')

if __name__ == '__main__':
    main()
