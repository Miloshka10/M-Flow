from io import BytesIO
import os
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from attachment_text import FULL_TEXT, extract_excerpt
from rich_document_reader import OCR_STATUS


def workbook(sheet, *, shared='', name='Измерения'):
    root = BytesIO()
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with ZipFile(root, 'w') as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns:r="{rel}"><sheets><sheet name="{name}" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels',
                         f'<Relationships><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet><sheetData>{sheet}</sheetData></worksheet>')
        if shared:
            archive.writestr('xl/sharedStrings.xml', f'<sst>{shared}</sst>')
    return root.getvalue()


def text_pdf(text):
    content = f'BT /F1 18 Tf 40 650 Td ({text}) Tj ET\n'.encode('ascii')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 700] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        f'<< /Length {len(content)} >>\nstream\n'.encode() + content + b'endstream',
    ]
    result, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(result)
    result.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:
        result.extend(f'{offset:010} 00000 n \n'.encode())
    result.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(result)


def scanned_work(kind):
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.new('RGB', (1300, 650), 'white')
    font = ImageFont.truetype('arial.ttf', 66) if os.name == 'nt' else ImageFont.load_default(size=66)
    label = 'Замеры датчика' if os.name == 'nt' else 'Sensor readings'
    ImageDraw.Draw(canvas).text((65, 200), f'{label} 21.4 22.5 23.6', fill='black', font=font)
    data = BytesIO()
    canvas.save(data, kind)
    return data.getvalue()


class RichAttachmentTests(unittest.TestCase):
    def test_text_pdf_reaches_the_extractor_with_a_page_reference(self):
        text, coverage = extract_excerpt('report.pdf', text_pdf('Three sensor readings: 20.1, 20.2, 20.3.'))
        self.assertEqual(coverage, FULL_TEXT)
        self.assertIn('Страница 1', text)
        self.assertIn('20.1, 20.2, 20.3', text)

    def test_spreadsheet_reads_labels_cached_values_and_cell_references(self):
        table = workbook('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>20.1</v></c></row>'
                         '<row r="2"><c r="A2" t="inlineStr"><is><t>Итог</t></is></c>'
                         '<c r="B2"><f>AVERAGE(B1:B1)</f><v>20.1</v></c></row>',
                         shared='<si><t>Температура, °C</t></si>')
        text, coverage = extract_excerpt('table.xlsx', table)
        self.assertEqual(coverage, FULL_TEXT)
        self.assertIn('Измерения', text)
        self.assertIn('A1: Температура, °C | B1: 20.1', text)
        self.assertIn('A2: Итог | B2: 20.1', text)
        self.assertNotIn('AVERAGE', text)

    def test_document_limits_and_worker_timeout_are_explicit(self):
        rows = ''.join(f'<row><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, 31))
        text, coverage = extract_excerpt('table.xlsx', workbook(rows))
        self.assertIn('A25: 25', text)
        self.assertNotIn('A26:', text)
        self.assertIn('часть', coverage)
        self.assertEqual(extract_excerpt('invalid.pdf', b'bad')[0], '')
        from subprocess import TimeoutExpired
        with patch('attachment_text.subprocess.run', side_effect=TimeoutExpired('worker', 32)):
            self.assertIn('время', extract_excerpt('report.pdf', b'pdf')[1])

    @unittest.skipUnless(os.environ.get('M_FLOW_TEST_OCR') == '1', 'Requires a local OCR engine.')
    def test_image_and_scanned_pdf_through_installed_ocr(self):
        for kind in ('PNG', 'PDF'):
            with self.subTest(kind=kind):
                text, coverage = extract_excerpt('scan.' + kind.lower(), scanned_work(kind))
                self.assertIn('21.4', text)
                self.assertIn('22.5', text)
                if os.name == 'nt':
                    self.assertIn('Замеры датчика', text)
                self.assertEqual(coverage, OCR_STATUS)
