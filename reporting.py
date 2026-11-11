"""Excel report builder for LinkScope V2."""
from __future__ import annotations

import io
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from datetime import datetime, timezone

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties

NAVY = '172A43'
TEAL = '047F81'
BLUE = '235CC3'
GRAY = '6C7D90'
LIGHT = 'EDF3F7'
GREEN = '16714B'
AMBER = 'BA7116'


def safe_cell(value):
    if value is None:
        return ''
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    # Imported page text is untrusted. Prevent spreadsheet formula injection.
    if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text
    return text


def add_tab(wb, title: str, df: pd.DataFrame, widths: dict[str, int] | None = None):
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False
    headers = list(df.columns)
    for col, name in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=str(name))
        cell.fill = PatternFill('solid', fgColor=NAVY)
        cell.font = Font(name='Aptos', size=10, bold=True, color='FFFFFF')
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 32
    for ridx, row in enumerate(df.itertuples(index=False, name=None), 2):
        for cidx, value in enumerate(row, 1):
            cell = ws.cell(row=ridx, column=cidx, value=safe_cell(value))
            cell.font = Font(name='Aptos', size=10, color='202C3B')
            cell.alignment = Alignment(vertical='top', wrap_text=(headers[cidx - 1] in ('Existing anchor','Evidence / existing sentence','Exact placement','QA Flags','Reason','Notes','Title', 'H1')))
            if ridx % 2 == 0:
                cell.fill = PatternFill('solid', fgColor='F4F7F9')
            if headers[cidx - 1].endswith('URL') or headers[cidx - 1] == 'URL':
                raw = str(value)
                if raw.startswith(('https://', 'http://')):
                    cell.hyperlink = raw
                    cell.font = Font(name='Aptos', size=10, color=BLUE, underline='single')
            if headers[cidx - 1] == 'Priority':
                priority = str(value)
                if priority == 'High':
                    cell.font = Font(name='Aptos', size=10, color=GREEN, bold=True)
                elif priority == 'Medium':
                    cell.font = Font(name='Aptos', size=10, color=AMBER, bold=True)
            if headers[cidx - 1] == 'Relevance Score':
                cell.number_format = '0.00'
    last_row = max(2, ws.max_row)
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{last_row}'
    ws.freeze_panes = 'C2' if len(headers) >= 6 else 'A2'
    for idx, label in enumerate(headers, 1):
        width = (widths or {}).get(label, 22)
        ws.column_dimensions[get_column_letter(idx)].width = min(65, max(12, width))
    return ws


def create_report(pages_df: pd.DataFrame, suggested_df: pd.DataFrame, warnings: list[str] | None = None) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = 'Overview'
    summary.sheet_view.showGridLines = False
    summary.merge_cells('A1:F2')
    summary['A1'] = 'LINKSCOPE | INTERNAL LINK AUDIT'
    summary['A1'].font = Font(name='Aptos Display', size=20, color='FFFFFF', bold=True)
    summary['A1'].fill = PatternFill('solid', fgColor=NAVY)
    summary['A1'].alignment = Alignment(vertical='center', indent=1)
    for row in range(1,3):
        summary.row_dimensions[row].height = 26
    summary.merge_cells('A4:F4')
    summary['A4'] = 'Report generated: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    summary['A4'].font = Font(size=10, color=GRAY)
    summary['A6'], summary['B6'] = 'Pages read', f'=COUNTA(\'Pages Audited\'!A2:A{max(2, len(pages_df)+1)})'
    summary['A7'], summary['B7'] = 'Candidate links', f'=COUNTA(\'Suggestions\'!A2:A{max(2, len(suggested_df)+1)})'
    summary['A8'], summary['B8'] = 'High-confidence candidates', f'=COUNTIF(\'Suggestions\'!F2:F{max(2, len(suggested_df)+1)},"High")'
    for row in (6, 7, 8):
        summary[f'A{row}'].font = Font(name='Aptos', size=12, color=NAVY, bold=True)
        summary[f'B{row}'].font = Font(name='Aptos', size=14, color=TEAL, bold=True)
    summary.merge_cells('A11:F11')
    summary['A11'] = 'Only verbatim anchor text found in existing unlinked paragraph/list copy is recommended.'
    summary.merge_cells('A12:F12')
    summary['A12'] = 'Each link requires manual relevance review. No links have been added to any website.'
    summary.merge_cells('A13:F13')
    summary['A13'] = 'Incoming link counts are observed only among pages in this audit; zero does not prove an orphan page.'
    for row in (11,12,13):
        summary[f'A{row}'].alignment = Alignment(wrap_text=True)
        summary[f'A{row}'].font = Font(size=10, color=GRAY)
        summary.row_dimensions[row].height = 30
    for i, warning in enumerate((warnings or [])[:8], 16):
        summary.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        summary.cell(i, 1, safe_cell('Note: ' + warning))
        summary.cell(i, 1).font = Font(size=10, color=AMBER)
        summary.cell(i, 1).alignment = Alignment(wrap_text=True)
        summary.row_dimensions[i].height = 32
    for col in 'ABCDEF':
        summary.column_dimensions[col].width = 22
    add_tab(wb, 'Suggestions', suggested_df, {
        'Source URL': 48, 'Source Title': 31, 'Existing anchor': 25,
        'Target URL': 48, 'Target Title': 31, 'Priority': 13,
        'Relevance Score': 16, 'Evidence / existing sentence': 65,
        'Exact placement': 62, 'QA Flags': 52,
        'Reason': 55, 'Review Status': 17, 'Owner': 18, 'Notes': 38,
    })
    add_tab(wb, 'Pages Audited', pages_df, {'URL': 55, 'Title': 38, 'H1': 35, 'Source': 20, 'Content review': 39,
                                           'Link graph status': 41, 'Canonical': 48})
    graph_cols = ['URL','Incoming links in audited pages','Existing outgoing links',
                  'Link graph status','Content review']
    add_tab(wb, 'Observed Link Graph', pages_df[graph_cols],
            {'URL': 57,'Incoming links in audited pages': 24,
             'Existing outgoing links': 25,'Link graph status': 45,'Content review': 42})
    guidance = pd.DataFrame([
        {'Step': '1', 'Instruction': 'Filter Suggestions by Source URL. Compare suggested anchors to live body content.'},
        {'Step': '2', 'Instruction': 'Check that each destination genuinely answers the current paragraph intent.'},
        {'Step': '3', 'Instruction': 'Reject generic, repetitive, ambiguous, testimonial, contact, or other unnatural placements.'},
        {'Step': '4', 'Instruction': 'Check duplicate links live, especially when CSV input lacked Existing Links.'},
        {'Step': '5', 'Instruction': 'Apply only approved links in the CMS; re-crawl to verify the change.'},
        {'Step': '6', 'Instruction': 'A zero incoming-link count is only within this audited set, never a proven sitewide orphan.'},
        {'Step': '7', 'Instruction': 'Never treat a score as a prediction of ranking gain or guaranteed SEO impact.'},
    ])
    add_tab(wb, 'Review Guide', guidance, {'Step': 12, 'Instruction': 115})
    wb.calculation = CalcProperties(calcMode='auto', fullCalcOnLoad=True)
    output = io.BytesIO()
    wb.save(output)
    # Populate cached values alongside the Excel formulas so preview apps show
    # correct counts before a desktop spreadsheet engine recalculates them.
    return _with_cached_overview(output.getvalue(), {
        'B6': len(pages_df),
        'B7': len(suggested_df),
        'B8': int((suggested_df['Priority'] == 'High').sum()) if 'Priority' in suggested_df else 0,
    })


def _with_cached_overview(workbook: bytes, values: dict[str, int]) -> bytes:
    """Store valid Excel formula caches in Overview without a paid calc engine."""
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    ET.register_namespace('', namespace)
    ET.register_namespace('r', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')
    src = io.BytesIO(workbook)
    dst = io.BytesIO()
    with ZipFile(src, 'r') as old, ZipFile(dst, 'w') as new:
        for info in old.infolist():
            data = old.read(info.filename)
            if info.filename == 'xl/worksheets/sheet1.xml':
                root = ET.fromstring(data)
                for cell in root.iter(f'{{{namespace}}}c'):
                    address = cell.get('r', '')
                    if address not in values:
                        continue
                    val = cell.find(f'{{{namespace}}}v')
                    if val is None:
                        val = ET.SubElement(cell, f'{{{namespace}}}v')
                    val.text = str(values[address])
                data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            new.writestr(info, data)
    return dst.getvalue()
