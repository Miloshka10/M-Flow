"""Presentation escaping; no app import or database initialization."""
import unittest
from interface import document_title


class DocumentTitleTests(unittest.TestCase):
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
