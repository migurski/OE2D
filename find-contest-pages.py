'''Categorize election source data using LLM-based extraction.

Usage: categorize-source.py <filename> <page_number>

Page numbers are 1-based. For XLSX/XLS files, page = sheet number.
For PDF files, page = PDF page number.
'''
from __future__ import annotations

import dataclasses
import os
import typing
import pydantic
import source_table


@dataclasses.dataclass
class ContestPage:
    contest_name: typing.Literal['President', 'U.S. Senate', 'U.S. House'] = pydantic.Field(description='Contest name')
    district_number: str | None = pydantic.Field(description='District number (for legislative races)')
    page_number: int = pydantic.Field(description='One-based page number')


def main() -> None:
    import argparse

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Read tabular data from a page of a source file.',
    )
    parser.add_argument('filename', help='Path to the source file')
    args: argparse.Namespace = parser.parse_args()

    import dspy, cmpnd

    class ContestFinder (dspy.Signature):
        """Identify all the pages for contests throughout the file.

        Tables generally list the contest name at the top, with an individual candidate
        in each column. The precincts are typically in a column to the left, and votes
        are often broken up into types like day-of or by-mail. Sometimes a single page
        lists separate contests side-by-side.

        We want a list of contests and page numbers where those contests can be found in
        the file. Not all pages have contests we care about!
        """
        context: str = dspy.InputField()
        table_file_path: str = dspy.InputField()
        contest_pages: list[ContestPage] = dspy.OutputField()

    sonnet45 = dspy.LM("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    dspy.configure(lm=sonnet45)
    detailer = dspy.RLM(ContestFinder, tools=[source_table.page_count, source_table.page_table], verbose=True)

    # Configure cmpnd tracing if API key is available
    cmpnd_key = os.environ.get("CMPND_API_KEY")
    if cmpnd_key:
        cmpnd.configure(api_key=cmpnd_key, project_tags=["ballotpedia-contest-pages"])
        cmpnd.auto_instrument()

    result = detailer(context='', table_file_path=args.filename)
    print(result)


if __name__ == '__main__':
    main()
