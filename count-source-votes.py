'''Categorize election source data using LLM-based extraction.

Usage: categorize-source.py <filename> <page_number>

Page numbers are 1-based. For XLSX/XLS files, page = sheet number.
For PDF files, page = PDF page number.
'''
from __future__ import annotations

import dataclasses
import os
import typing

from oe2d import source_table


@dataclasses.dataclass
class CountDetail:
    contest_name: typing.Literal['US President', 'US Senate', 'US House']
    district_number: str | None
    precinct_name: str
    candidate_name: str
    candidate_party: typing.Literal['Democratic', 'Republican', 'Libertarian', 'Green Party', 'Other']
    vote_count: int
    vote_type: typing.Literal['Early Voting', 'Election Day', 'Vote by Mail', 'Total Votes']


def main() -> None:
    import argparse

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Read tabular data from a page of a source file.',
    )
    parser.add_argument('filename', help='Path to the source file')
    parser.add_argument('page', type=int, help='Page number (1-based)')
    args: argparse.Namespace = parser.parse_args()

    import dspy, cmpnd

    class CountDetailer (dspy.Signature):
        """Identify all the vote count details in a table on the given page of the file.

        Tables generally list the contest name at the top, with an individual candidate
        in each column. The precincts are typically in a column to the left, and votes
        are often broken up into types like day-of or by-mail. Sometimes a single page
        lists separate contests side-by-side.

        Numbers of votes can be found in individual table cells.
        """
        context: str = dspy.InputField()
        table_file_path: str = dspy.InputField()
        table_file_page: int = dspy.InputField()
        count_details: list[CountDetail] = dspy.OutputField()

    sonnet45 = dspy.LM("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    dspy.configure(lm=sonnet45)
    detailer = dspy.RLM(CountDetailer, tools=[source_table.page_table], verbose=True)

    # Configure cmpnd tracing if API key is available
    cmpnd_key = os.environ.get("CMPND_API_KEY")
    if cmpnd_key:
        cmpnd.configure(api_key=cmpnd_key, project_tags=["ballotpedia-candidates"])
        cmpnd.auto_instrument()

    result = detailer(context='', table_file_path=args.filename, table_file_page=args.page)
    print(result)


if __name__ == '__main__':
    main()
