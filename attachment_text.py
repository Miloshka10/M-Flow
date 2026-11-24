"""Read bounded text from classroom files without opening external resources."""
from io import BytesIO
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zlib import error as ZipDataError

OFFICE = frozenset({'docx', 'pptx'})
PLAIN = frozenset('txt md csv tsv py js ts jsx tsx html css json xml sql java c h cpp ino'.split())
EXCERPT_SIZE = 1200
XML_LIMIT = 768 * 1024
SLIDE = re.compile(r'ppt/slides/slide([1-9][0-9]{0,2})\.xml\Z')
WORD = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
DRAWING = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def extension(filename):
    return PurePosixPath(filename).suffix.lower().lstrip('.')


def office_text(content, kind):
    with ZipFile(BytesIO(content)) as archive:
        items = archive.infolist()
        if len(items) > 500:
            raise ValueError('Слишком много частей документа.')
        if kind == 'docx':
            members = [item for item in items if item.filename == 'word/document.xml']
            namespace = WORD
        else:
            members = [item for item in items if SLIDE.fullmatch(item.filename)]
            members.sort(key=lambda item: int(SLIDE.fullmatch(item.filename)[1]))
            namespace = DRAWING
        if (not members or len(members) > 40 or len({item.filename for item in members}) != len(members)
                or sum(item.file_size for item in members) > XML_LIMIT):
            raise ValueError('Структура документа превышает предел разбора.')
        paragraphs = []
        remaining = XML_LIMIT
        for item in members:
            with archive.open(item) as source:
                xml = source.read(remaining + 1)
            remaining -= len(xml)
            if remaining < 0 or b'\0' in xml or re.search(rb'<!\s*(DOCTYPE|ENTITY)\b', xml, re.I):
                raise ValueError('Неподдерживаемая структура документа.')
            root = ElementTree.fromstring(xml.decode('utf-8-sig'))
            for paragraph in root.iter(namespace + 'p'):
                line = ''.join(node.text or '' for node in paragraph.iter(namespace + 't')).strip()
                if line:
                    paragraphs.append(line)
    return '\n'.join(paragraphs)


def extract_excerpt(filename, content):
    kind = extension(filename)
    if kind not in OFFICE | PLAIN:
        return '', 'Формат доступен для скачивания'
    try:
        text = office_text(content, kind) if kind in OFFICE else content.decode('utf-8-sig')
    except (BadZipFile, RuntimeError, EOFError, NotImplementedError, ValueError, ElementTree.ParseError,
            ZipDataError):
        return '', 'Текст извлечь не удалось'
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text or '\0' in text:
        return '', 'Нет доступного текста'
    return text[:EXCERPT_SIZE], 'Прочитан фрагмент текста' if len(text) > EXCERPT_SIZE else 'Текст прочитан'
