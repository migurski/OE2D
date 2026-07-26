'''Tests for oe2d.source_table — the deterministic tabular reader.'''
import os
import unittest

from ... import source_table

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FIXTURES_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'source_table', 'fixtures')


def _fixture(filename: str) -> str:
    return os.path.join(FIXTURES_DIR, filename)


class TestXlsxSanFrancisco(unittest.TestCase):
    '''San Francisco County - Statement of the Vote - General 2024.xlsx

    Fixture: sheet 2 extracted as single-sheet XLSX.
    '''

    def setUp(self):
        self.path = _fixture('sf-xlsx-sheet2.xlsx')

    def test_page_2_has_contest_header(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        self.assertIn('PRESIDENT AND VICE PRESIDENT', rows[1][0])

    def test_page_2_has_candidate_columns(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        row_text = ' '.join(rows[3])
        self.assertIn('DONALD J. TRUMP', row_text)
        self.assertIn('KAMALA D. HARRIS', row_text)

    def test_page_2_merged_cells_expanded(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        contest_row = rows[1]
        filled = [c for c in contest_row if 'PRESIDENT' in c]
        self.assertGreater(len(filled), 1)


class TestXlsxAlamedaDistrict(unittest.TestCase):
    '''2024 Alameda County, CA district-level results.xlsx

    Fixture: sheet 5 extracted as single-sheet XLSX.
    '''

    def setUp(self):
        self.path = _fixture('alameda-district-xlsx-sheet5.xlsx')

    def test_sheet_5_contest_title_expanded(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        row_text = ' '.join(rows[5])
        self.assertIn('U.S. Representative', row_text)
        filled = [c for c in rows[5] if 'U.S. Representative' in c]
        self.assertGreater(len(filled), 1)

    def test_sheet_5_has_data_rows(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        data_texts = [r[0] for r in rows if 'California' in r[0] or 'District' in r[0]]
        self.assertGreater(len(data_texts), 0)


class TestXlsSantaClara(unittest.TestCase):
    '''Santa Clara - Precint results - General 2024.xls (XML Spreadsheet)

    Fixture: sheets 2-3 extracted, 100 rows each.
    '''

    def setUp(self):
        self.path = _fixture('santa-clara-xls-sheets2-3.xls')

    def test_page_2_registered_voters(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        self.assertEqual(rows[0][0], 'Precinct')
        self.assertEqual(rows[0][1], 'Registered Voters')
        self.assertEqual(rows[0][2], 'Ballots Cast')

    def test_page_2_has_precinct_data(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        self.assertEqual(rows[1][0], '0002001')

    def test_page_3_president_contest(self):
        rows = source_table.page_table(self.path, 2)
        self.assertIsNotNone(rows)
        self.assertIn('President and Vice President', rows[0][0])
        row_text = ' '.join(rows[1])
        self.assertIn('Kamala D. Harris', row_text)
        self.assertIn('Donald J. Trump', row_text)


class TestPdfAlameda(unittest.TestCase):
    '''Alameda County - Statement of Vote - General Election.pdf

    Counterclockwise-rotated vertical text headers.
    Fixture: pages 1 and 101 extracted.
    '''

    def setUp(self):
        self.path = _fixture('alameda-sov-pdf-p1-p101.pdf')

    def test_page_1_vertical_headers_reconstructed(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:3])
        self.assertIn('DONALD J. TRUMP', all_text)
        self.assertIn('KAMALA D. HARRIS', all_text)
        self.assertIn('Registered Voters', all_text)

    def test_page_1_contest_title(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        self.assertIn('President', rows[0][0])

    def test_page_1_data_rows_individual(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        data_rows = [r for r in rows if r[0].strip().isdigit()]
        self.assertGreater(len(data_rows), 0)
        for r in data_rows[:5]:
            self.assertNotIn('\n', r[0])

    def test_page_1_no_garbled_headers(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        for r in rows[:5]:
            row_text = ' '.join(r)
            self.assertNotIn('DLANOD', row_text)

    def test_page_101_us_senator(self):
        rows = source_table.page_table(self.path, 2)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:3])
        self.assertIn('STEVE GARVEY', all_text)
        self.assertIn('ADAM B. SCHIFF', all_text)


class TestPdfAmador(unittest.TestCase):
    '''Amador County - Statement of the Vote - General 2024.pdf

    Landscape pages with counterclockwise-rotated headers and
    multi-contest side-by-side layout.
    Fixture: pages 5, 7, 10, 30 extracted.
    '''

    def setUp(self):
        self.path = _fixture('amador-pdf-p5-p7-p10-p30.pdf')

    def test_page_5_statistics(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:6])
        self.assertIn('Registered Voters', all_text)
        self.assertIn('Ballots Cast', all_text)

    def test_page_7_president_single_contest(self):
        rows = source_table.page_table(self.path, 2)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:6])
        self.assertIn('DONALD J. TRUMP', all_text)
        self.assertIn('KAMALA D. HARRIS', all_text)
        self.assertIn('President', all_text)

    def test_page_7_precinct_data(self):
        rows = source_table.page_table(self.path, 2)
        self.assertIsNotNone(rows)
        cp_rows = [r for r in rows if r[0].startswith('CP')]
        self.assertGreater(len(cp_rows), 0)
        self.assertEqual(cp_rows[0][0], 'CP10')

    def test_page_10_two_senator_contests(self):
        rows = source_table.page_table(self.path, 3)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:6])
        self.assertIn('United States Senator - Full Term', all_text)
        self.assertIn('United States Senator - Partial/Unexpired Term', all_text)

    def test_page_10_contest_titles_split_columns(self):
        rows = source_table.page_table(self.path, 3)
        self.assertIsNotNone(rows)
        senator_row = None
        for r in rows:
            if 'United States Senator - Full Term' in r:
                senator_row = r
                break
        self.assertIsNotNone(senator_row)
        self.assertIn('United States Senator - Full Term', senator_row)
        self.assertIn('United States Senator - Partial/Unexpired Term', senator_row)
        first_full = senator_row.index('United States Senator - Full Term')
        first_partial = senator_row.index('United States Senator - Partial/Unexpired Term')
        self.assertLess(first_full, first_partial)

    def test_page_10_has_all_precincts(self):
        rows = source_table.page_table(self.path, 3)
        self.assertIsNotNone(rows)
        cp_rows = [r for r in rows if r[0].startswith('CP')]
        self.assertEqual(len(cp_rows), 17)

    def test_page_30_two_propositions(self):
        rows = source_table.page_table(self.path, 4)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:6])
        self.assertIn('Proposition 6', all_text)
        self.assertIn('Proposition 32', all_text)

    def test_page_10_vote_for_1_row(self):
        rows = source_table.page_table(self.path, 3)
        self.assertIsNotNone(rows)
        vote_rows = [r for r in rows if 'VOTE FOR 1' in r]
        self.assertGreater(len(vote_rows), 0)


class TestPdfGlenn(unittest.TestCase):
    '''Glenn County - Statement of the Vote - General 2024.pdf

    Clockwise-rotated vertical text (reads bottom-to-top).
    Fixture: page 15 extracted.
    '''

    def setUp(self):
        self.path = _fixture('glenn-pdf-p15.pdf')

    def test_page_15_headers_not_backwards(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:5])
        self.assertNotIn('ECNAV', all_text)
        self.assertNotIn('PMURT', all_text)
        self.assertNotIn('tsaC', all_text)

    def test_page_15_correct_headers(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:5])
        self.assertIn('Times Cast', all_text)
        self.assertIn('Registered Voters', all_text)
        self.assertIn('TRUMP', all_text)

    def test_page_15_contest_title(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows[:3])
        self.assertIn('President', all_text)


class TestPdfHumboldt(unittest.TestCase):
    '''Humboldt County - Final Precinct Report - General 2024.pdf

    No vertical lines — column boundaries derived from horizontal line
    segment endpoints. Multiple stacked contests per page with multi-line
    candidate names (e.g. "Donald J. Trump\\nand JD Vance").
    Fixture: pages 1 and 2 extracted.
    '''

    def setUp(self):
        self.path = _fixture('humboldt-p1-p2.pdf')

    def test_page_1_has_all_three_contests(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows)
        self.assertIn('PRESIDENT AND VICE PRESIDENT', all_text)
        self.assertIn('U.S. SENATOR, FULL TERM', all_text)
        self.assertIn('U.S. SENATOR, PARTIAL/UNEXPIRED', all_text)

    def test_page_1_president_candidates(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows)
        self.assertIn('Claudia De la Cruz', all_text)
        self.assertIn('Kamala D. Harris', all_text)
        self.assertIn('Donald J. Trump', all_text)

    def test_page_1_data_aligned(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        trump_row = [r for r in rows if 'Donald J. Trump' in r[0]]
        self.assertEqual(len(trump_row), 1)
        self.assertEqual(trump_row[0][1], '204')
        self.assertEqual(trump_row[0][-2], '1,155')

    def test_page_1_summary_rows_intact(self):
        rows = source_table.page_table(self.path, 1)
        self.assertIsNotNone(rows)
        cast_rows = [r for r in rows if r[0].startswith('Cast Votes')]
        self.assertEqual(len(cast_rows), 3)

    def test_page_2_has_three_contests(self):
        rows = source_table.page_table(self.path, 2)
        self.assertIsNotNone(rows)
        all_text = ' '.join(' '.join(r) for r in rows)
        self.assertIn('U.S. REPRESENTATIVE DISTRICT 2', all_text)
        self.assertIn('STATE ASSEMBLY DISTRICT 2', all_text)
        self.assertIn('HUMBOLDT COMMUNITY SERVICES DIS', all_text)

    def test_page_count(self):
        self.assertEqual(source_table.page_count(self.path), 2)


class TestPageTableRouting(unittest.TestCase):
    '''Test that source_table.page_table routes correctly and handles errors.'''

    def test_unsupported_extension(self):
        rows = source_table.page_table('test.doc', 1)
        self.assertIsNone(rows)

    def test_xlsx_out_of_range(self):
        path = _fixture('sf-xlsx-sheet2.xlsx')
        rows = source_table.page_table(path, 9999)
        self.assertIsNone(rows)

    def test_pdf_out_of_range(self):
        path = _fixture('alameda-sov-pdf-p1-p101.pdf')
        rows = source_table.page_table(path, 99999)
        self.assertIsNone(rows)


class TestPageCount(unittest.TestCase):
    '''Test source_table.page_count for all fixture file types.'''

    def test_xlsx_sf(self):
        self.assertEqual(source_table.page_count(_fixture('sf-xlsx-sheet2.xlsx')), 1)

    def test_xlsx_alameda_district(self):
        self.assertEqual(source_table.page_count(_fixture('alameda-district-xlsx-sheet5.xlsx')), 1)

    def test_xls_santa_clara(self):
        self.assertEqual(source_table.page_count(_fixture('santa-clara-xls-sheets2-3.xls')), 2)

    def test_pdf_glenn(self):
        self.assertEqual(source_table.page_count(_fixture('glenn-pdf-p15.pdf')), 1)

    def test_pdf_amador(self):
        self.assertEqual(source_table.page_count(_fixture('amador-pdf-p5-p7-p10-p30.pdf')), 4)

    def test_pdf_alameda_sov(self):
        self.assertEqual(source_table.page_count(_fixture('alameda-sov-pdf-p1-p101.pdf')), 2)

    def test_unsupported_extension(self):
        self.assertEqual(source_table.page_count('test.doc'), 0)


class TestPageTables(unittest.TestCase):
    '''Test source_table.page_tables with different strategies and fixtures.'''

    def test_humboldt_lines_finds_multiple_tables(self):
        path = _fixture('humboldt-p1-p2.pdf')
        tables = source_table.page_tables(path, 1, 'lines')
        self.assertIsNotNone(tables)
        self.assertGreater(len(tables), 1)

    def test_amador_lines_finds_one_table(self):
        path = _fixture('amador-pdf-p5-p7-p10-p30.pdf')
        tables = source_table.page_tables(path, 2, 'lines')
        self.assertIsNotNone(tables)
        self.assertEqual(len(tables), 1)

    def test_bbox_is_bbox_instance(self):
        path = _fixture('humboldt-p1-p2.pdf')
        tables = source_table.page_tables(path, 1, 'lines')
        self.assertIsNotNone(tables)
        bbox = tables[0].bbox
        self.assertIsInstance(bbox, source_table.BBox)
        self.assertIsInstance(bbox.x0, float)
        self.assertIsInstance(bbox.top, float)
        self.assertIsInstance(bbox.x1, float)
        self.assertIsInstance(bbox.bottom, float)

    def test_preview_capped_at_three_rows(self):
        path = _fixture('amador-pdf-p5-p7-p10-p30.pdf')
        tables = source_table.page_tables(path, 2, 'lines')
        self.assertIsNotNone(tables)
        self.assertLessEqual(len(tables[0].preview), 3)

    def test_strategy_recorded(self):
        path = _fixture('humboldt-p1-p2.pdf')
        tables = source_table.page_tables(path, 1, 'text')
        self.assertIsNotNone(tables)
        self.assertEqual(tables[0].strategy, 'text')

    def test_non_pdf_returns_none(self):
        path = _fixture('sf-xlsx-sheet2.xlsx')
        self.assertIsNone(source_table.page_tables(path, 1, 'lines'))

    def test_out_of_range_returns_none(self):
        path = _fixture('humboldt-p1-p2.pdf')
        self.assertIsNone(source_table.page_tables(path, 999, 'lines'))


class TestPageWords(unittest.TestCase):
    '''Test source_table.page_words for PDF fixtures.'''

    def test_returns_word_dicts(self):
        path = _fixture('humboldt-p1-p2.pdf')
        words = source_table.page_words(path, 1)
        self.assertIsNotNone(words)
        self.assertGreater(len(words), 0)
        self.assertIn('text', words[0])
        self.assertIn('x0', words[0])
        self.assertIn('top', words[0])
        self.assertIn('bottom', words[0])
        self.assertIn('upright', words[0])

    def test_president_in_words(self):
        path = _fixture('humboldt-p1-p2.pdf')
        words = source_table.page_words(path, 1)
        self.assertIsNotNone(words)
        all_text = ' '.join(w['text'] for w in words)
        self.assertIn('PRESIDENT', all_text)

    def test_coordinates_are_rounded(self):
        path = _fixture('humboldt-p1-p2.pdf')
        words = source_table.page_words(path, 1)
        self.assertIsNotNone(words)
        for w in words[:10]:
            self.assertEqual(w['x0'], round(w['x0'], 1))
            self.assertEqual(w['top'], round(w['top'], 1))

    def test_non_pdf_returns_none(self):
        path = _fixture('sf-xlsx-sheet2.xlsx')
        self.assertIsNone(source_table.page_words(path, 1))

    def test_out_of_range_returns_none(self):
        path = _fixture('humboldt-p1-p2.pdf')
        self.assertIsNone(source_table.page_words(path, 999))


if __name__ == '__main__':
    unittest.main()
