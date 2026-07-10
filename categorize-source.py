'''Read tabular data from a page of a source file.

Usage: categorize-source.py <filename> <page_number>

Page numbers are 1-based. For XLSX/XLS files, page = sheet number.
For PDF files, page = PDF page number.
'''
import argparse
import csv
import io
import os
import sys
import xml.etree.ElementTree as ET


def read_xlsx_page(path, page):
    '''Read a sheet from an XLSX file, return rows as lists of strings.'''
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if page < 1 or page > len(wb.sheetnames):
        print(f'Page {page} out of range (1-{len(wb.sheetnames)})', file=sys.stderr)
        return None
    ws = wb.worksheets[page - 1]
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append([str(v) if v is not None else '' for v in row])
    wb.close()
    return rows


def read_xls_page(path, page):
    '''Read a sheet from an XLS file, return rows as lists of strings.

    Handles both binary BIFF format (via xlrd) and XML Spreadsheet format.
    '''
    with open(path, 'rb') as f:
        head = f.read(20)

    if head.lstrip(b'\xef\xbb\xbf').startswith(b'<?xml'):
        return _read_xml_spreadsheet_page(path, page)

    return _read_xlrd_page(path, page)


def _read_xlrd_page(path, page):
    '''Read a sheet from a binary XLS file using xlrd.'''
    import xlrd

    wb = xlrd.open_workbook(path)
    if page < 1 or page > wb.nsheets:
        print(f'Page {page} out of range (1-{wb.nsheets})', file=sys.stderr)
        return None
    ws = wb.sheet_by_index(page - 1)
    rows = []
    for i in range(ws.nrows):
        rows.append([str(ws.cell_value(i, j)) if ws.cell_value(i, j) != '' else ''
                      for j in range(ws.ncols)])
    return rows


def _read_xml_spreadsheet_page(path, page):
    '''Read a sheet from an XML Spreadsheet (SpreadsheetML) file.'''
    ns = {'s': 'urn:schemas-microsoft-com:office:spreadsheet'}
    tree = ET.parse(path)
    root = tree.getroot()
    sheets = root.findall('.//s:Worksheet', ns)

    if page < 1 or page > len(sheets):
        print(f'Page {page} out of range (1-{len(sheets)})', file=sys.stderr)
        return None

    ws = sheets[page - 1]
    rows = []
    for row_el in ws.findall('.//s:Row', ns):
        cells = []
        col_index = 0
        for cell_el in row_el.findall('s:Cell', ns):
            # Handle ss:Index attribute for skipped columns
            index_attr = cell_el.attrib.get(
                '{urn:schemas-microsoft-com:office:spreadsheet}Index'
            )
            if index_attr is not None:
                target = int(index_attr) - 1
                while col_index < target:
                    cells.append('')
                    col_index += 1
            data_el = cell_el.find('s:Data', ns)
            cells.append(data_el.text if data_el is not None and data_el.text else '')
            col_index += 1
        rows.append(cells)
    return rows


def read_pdf_page(path, page):
    '''Read a table from a PDF page, return rows as lists of strings.'''
    import pdfplumber

    pdf = pdfplumber.open(path)
    if page < 1 or page > len(pdf.pages):
        print(f'Page {page} out of range (1-{len(pdf.pages)})', file=sys.stderr)
        return None

    pdf_page = pdf.pages[page - 1]
    tables = pdf_page.extract_tables()
    if not tables:
        print(f'No tables found on page {page}', file=sys.stderr)
        return None

    # Use the largest table on the page
    table = max(tables, key=len)
    rows = []
    for row in table:
        rows.append([str(v) if v is not None else '' for v in row])
    pdf.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description='Read tabular data from a page of a source file.')
    parser.add_argument('filename', help='Path to the source file')
    parser.add_argument('page', type=int, help='Page number (1-based)')
    args = parser.parse_args()

    ext = os.path.splitext(args.filename)[1].lower()

    if ext == '.xlsx':
        rows = read_xlsx_page(args.filename, args.page)
    elif ext == '.xls':
        rows = read_xls_page(args.filename, args.page)
    elif ext == '.pdf':
        rows = read_pdf_page(args.filename, args.page)
    else:
        print(f'Unsupported file type: {ext}', file=sys.stderr)
        sys.exit(1)

    if rows is None:
        sys.exit(1)

    try:
        writer = csv.writer(sys.stdout)
        for row in rows:
            writer.writerow(row)
        sys.stdout.flush()
    except BrokenPipeError:
        pass


if __name__ == '__main__':
    main()
