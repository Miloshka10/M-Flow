"""Presentation escaping; no app import or database initialization."""
import unittest
from pathlib import Path
import re
from urllib.parse import unquote
from xml.etree import ElementTree
from interface import document_title


class DocumentTitleTests(unittest.TestCase):
    def test_css_icon_images_are_standalone_svg_documents(self):
        # Inline HTML accepts SVG without xmlns; CSS image decoding requires it.
        css = (Path(__file__).resolve().parents[1] / 'static' / 'icon-symbols.css').read_text(encoding='utf-8')
        images = re.findall(r'url\("data:image/svg\+xml,([^\"]+)"\)', css)
        self.assertTrue(images)
        for image in images:
            root = ElementTree.fromstring(unquote(image))
            self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
            self.assertEqual(root.get('viewBox'), '0 0 24 24')
            self.assertTrue(list(root))

    def test_user_text_cannot_break_out_of_document_title(self):
        title = document_title('<h1>&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</h1>')
        self.assertIn('&lt;/title&gt;', title)
        self.assertIn('&lt;script&gt;', title)
        self.assertNotIn('</title>', title)
        self.assertNotIn('<script>', title)

    def test_first_heading_is_bounded_and_empty_pages_have_a_title(self):
        self.assertEqual(document_title('<h1>Проект <span>класса</span></h1><h1>Другой</h1>'), 'Проект класса — M-Flow')
        self.assertLess(len(document_title('<h1>' + 'a'*1000 + '</h1>')), 200)
        self.assertEqual(document_title('<p>Сообщение</p>'), 'M-Flow — проекты и задачи')
