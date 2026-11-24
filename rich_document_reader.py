"""Isolated subprocess for PDF, spreadsheet and OCR text extraction."""
from base64 import b64encode
from contextlib import closing
from io import BytesIO
import json
import os
from pathlib import Path
from posixpath import normpath
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zlib import error as ZipDataError

from attachment_text import EXCERPT_SIZE, FULL_TEXT, safe_xml
from submission_attachments import FILE_SIZE

OCR_STATUS = 'Текст распознан. Сверьте с оригиналом'
PIXEL_LIMIT = 16_000_000
PDF_PAGES = 6
PDF_SCAN_PAGES = 2
SHEET_LIMIT = 4
ROW_LIMIT = 25
SHEET_PATH = re.compile(r'xl/worksheets/[^/]+\.xml\Z')
CELL_ADDRESS = re.compile(r'[A-Z]{1,3}[1-9][0-9]{0,6}\Z')
WINDOWS_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def xml_members(archive):
    items = archive.infolist()
    names = {item.filename for item in items}
    if len(items) > 500 or len(names) != len(items):
        raise ValueError('Invalid workbook package.')
    remaining = 2 * 1024 * 1024

    def read(name):
        nonlocal remaining
        if name not in names or archive.getinfo(name).file_size > remaining:
            raise ValueError('Workbook text exceeds extraction limits.')
        with archive.open(name) as source:
            content = source.read(remaining + 1)
        remaining -= len(content)
        if remaining < 0:
            raise ValueError('Workbook text exceeds extraction limits.')
        return safe_xml(content)

    return names, read


def string_value(element):
    return ''.join(part.text or '' for part in element.iter() if local_name(part.tag) == 't')


def sheet_cells(row, strings):
    cells = []
    for cell in row:
        address = cell.get('r', '')
        if local_name(cell.tag) != 'c' or not CELL_ADDRESS.fullmatch(address):
            continue
        kind = cell.get('t')
        if kind == 'inlineStr':
            value = string_value(cell)
        else:
            value = next((part.text or '' for part in cell if local_name(part.tag) == 'v'), '')
            if kind == 's':
                value = strings[int(value)] if value.isascii() and value.isdigit() and len(value) < 8 and int(value) < len(strings) else ''
        if value:
            cells.append(f'{address}: {" ".join(value.split())}')
    return cells


def spreadsheet_text(content):
    with ZipFile(BytesIO(content)) as archive:
        names, read = xml_members(archive)
        workbook = read('xl/workbook.xml')
        links = read('xl/_rels/workbook.xml.rels')
        targets = {}
        for link in links:
            if link.get('TargetMode') == 'External' or not link.get('Type', '').endswith('/worksheet'):
                continue
            target = link.get('Target', '')
            path = normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            if SHEET_PATH.fullmatch(path):
                targets[link.get('Id')] = path
        strings = [string_value(item) for item in read('xl/sharedStrings.xml')
                   if local_name(item.tag) == 'si'] if 'xl/sharedStrings.xml' in names else []
        sheets = [item for item in workbook.iter() if local_name(item.tag) == 'sheet' and item.get('state', 'visible') == 'visible']
        sections, limited = [], len(sheets) > SHEET_LIMIT
        for sheet in sheets[:SHEET_LIMIT]:
            identity = next((value for key, value in sheet.attrib.items() if local_name(key) == 'id'), None)
            if identity not in targets:
                limited = True
                continue
            lines = []
            for row in read(targets[identity]).iter():
                if local_name(row.tag) != 'row':
                    continue
                cells = sheet_cells(row, strings)
                if cells:
                    if len(lines) == ROW_LIMIT:
                        limited = True
                        break
                    limited |= len(cells) > 12
                    lines.append(' | '.join(cells[:12]))
            if lines:
                sections.append('Лист «' + sheet.get('name', '') + '»\n' + '\n'.join(lines))
        return '\n\n'.join(sections), 'Прочитана часть таблицы' if limited else FULL_TEXT


def ocr_command(image_path):
    binary = shutil.which('tesseract')
    if binary:
        language = os.environ.get('M_FLOW_OCR_LANG', 'rus+eng')
        return [binary, str(image_path), 'stdout', '-l', language, '--psm', '6'], os.environ
    if os.name == 'nt':
        script = Path(__file__).with_name('scripts') / 'ocr-windows.ps1'
        encoded = b64encode(script.read_text(encoding='utf-8').encode('utf-16-le')).decode('ascii')
        return ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], dict(
            os.environ, M_FLOW_OCR_IMAGE=str(image_path))
    return None, os.environ


def recognize(image):
    image.thumbnail((2000, 2000))
    with TemporaryDirectory(prefix='mflow-ocr-') as directory:
        image_path = Path(directory) / 'page.png'
        image.convert('L').save(image_path, 'PNG')
        command, env = ocr_command(image_path)
        if not command:
            return '', 'OCR на сервере не настроен'
        try:
            result = subprocess.run(command, env=env, timeout=8, capture_output=True, check=True,
                                    creationflags=WINDOWS_FLAGS)
        except (OSError, subprocess.SubprocessError):
            return '', 'OCR недоступен или превысил время обработки'
        text = result.stdout.decode('utf-8', errors='replace').strip()
        return (text, OCR_STATUS) if text else ('', 'OCR не нашёл читаемого текста')


def image_text(content):
    from PIL import Image, ImageOps

    with Image.open(BytesIO(content)) as image:
        if image.format not in {'PNG', 'JPEG', 'WEBP', 'TIFF'} or image.width * image.height > PIXEL_LIMIT:
            return '', 'Изображение превышает предел распознавания или имеет другой формат'
        return recognize(ImageOps.exif_transpose(image))


def pdf_text(content):
    import pypdfium2 as pdfium

    with closing(pdfium.PdfDocument(content)) as pdf:
        sections, recognized, omitted, unavailable = [], 0, len(pdf) > PDF_PAGES, ''
        scans = 0
        for number in range(min(len(pdf), PDF_PAGES)):
            with closing(pdf[number]) as page, closing(page.get_textpage()) as text_page:
                text = text_page.get_text_bounded().strip()
                if len(text) < 20:
                    if scans < PDF_SCAN_PAGES:
                        scans += 1
                        scale = min(2.25, 1800 / max(1, page.get_width(), page.get_height()))
                        with closing(page.render(scale=scale)) as bitmap:
                            recognized_text, status = recognize(bitmap.to_pil())
                        if recognized_text:
                            recognized += 1
                            text = recognized_text
                        else:
                            omitted, unavailable = True, status
                    else:
                        omitted = True
                if text:
                    sections.append(f'Страница {number + 1}\n{text}')
        if recognized:
            coverage = 'Распознана часть PDF. Сверьте с оригиналом' if omitted else OCR_STATUS
        else:
            coverage = unavailable or ('Прочитана часть PDF' if omitted else FULL_TEXT)
        return '\n\n'.join(sections), coverage


def main(kind):
    if os.name != 'nt':
        import resource

        # Bound native decoders independently of the Flask process.
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    content = sys.stdin.buffer.read(FILE_SIZE + 1)
    text, coverage = '', 'Текст извлечь не удалось'
    if 0 < len(content) <= FILE_SIZE:
        reader = {'pdf': pdf_text, 'xlsx': spreadsheet_text}.get(kind, image_text)
        try:
            text, coverage = reader(content)
        except ImportError:
            coverage = 'Модуль обработки формата не установлен'
        except (BadZipFile, KeyError, RuntimeError, ValueError, OSError, EOFError, ElementTree.ParseError, ZipDataError):
            pass
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\0', '').strip()
    if not text and coverage == FULL_TEXT:
        coverage = 'Нет доступного текста'
    if len(text) > EXCERPT_SIZE:
        if coverage == FULL_TEXT:
            coverage = 'Прочитан фрагмент текста'
        elif coverage == OCR_STATUS:
            coverage = 'Распознан фрагмент текста. Сверьте с оригиналом'
    sys.stdout.buffer.write(json.dumps(dict(text=text[:EXCERPT_SIZE], coverage=coverage), ensure_ascii=False).encode())


if __name__ == '__main__':
    main(sys.argv[1])
