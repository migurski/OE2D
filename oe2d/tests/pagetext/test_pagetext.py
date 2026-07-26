'''unit_text: routing a page/sheet to the cheapest text source.

source_table and OCR are mocked, so these pin the ROUTING (csv -> rows -> words ->
OCR fallback) without touching real files, LibreOffice, or tesseract.
'''
from ... import pagetext


def test_csv_reads_raw_lines(tmp_path):
    path = tmp_path / 'data.csv'
    path.write_text('Precinct,Harris,Trump\nWard 1,409,436\n')
    text = pagetext.unit_text(str(path), 1)
    assert 'Precinct,Harris,Trump' in text


def test_spreadsheet_joins_table_rows(monkeypatch):
    monkeypatch.setattr(pagetext.source_table, 'page_table',
                        lambda path, unit: [['Precinct', 'Harris'], ['Ward 1', '409']])
    text = pagetext.unit_text('x.xlsx', 1)
    assert 'Harris' in text and 'Ward 1' in text


def test_pdf_uses_words_when_no_table(monkeypatch):
    monkeypatch.setattr(pagetext.source_table, 'page_table', lambda path, unit: None)
    monkeypatch.setattr(pagetext.source_table, 'page_words',
                        lambda path, unit: [{'text': 'President'}, {'text': 'Harris'}])
    text = pagetext.unit_text('x.pdf', 1)
    assert 'President' in text and 'Harris' in text


def test_scanned_pdf_falls_back_to_ocr(monkeypatch):
    monkeypatch.setattr(pagetext.source_table, 'page_table', lambda path, unit: None)
    monkeypatch.setattr(pagetext.source_table, 'page_words', lambda path, unit: None)
    monkeypatch.setattr(pagetext, 'ocr_page', lambda path, unit: 'OCR President Harris')
    text = pagetext.unit_text('x.pdf', 1)
    assert text == 'OCR President Harris'
