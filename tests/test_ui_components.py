"""Presentation regression checks; workflow state remains owned by existing modules."""
import unittest
from html.parser import HTMLParser
from ui_components import notice, state_badge


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class UIComponentsTests(unittest.TestCase):
    def test_user_text_is_not_interpreted_as_markup_or_a_template(self):
        payload = '<img src=x onerror=alert(1)> {{ name }} & "текст"'
        for html in (notice(payload), notice(payload, error=True), state_badge(payload, 'review')):
            markup = Markup(html)
            self.assertEqual(len(markup.elements), 1)
            self.assertEqual(''.join(markup.text), payload)

    def test_results_and_project_badges_share_tones_without_changing_labels(self):
        for state in ('draft', 'review', 'revision', 'accepted', 'grade'):
            project = Markup(state_badge('Реальное состояние', state))
            result = Markup(state_badge('Реальное состояние', state, result=True))
            self.assertEqual(project.elements[0][1]['data-ui-tone'], result.elements[0][1]['data-ui-tone'])
            self.assertEqual(result.elements[0][1]['class'], 'result-state')
            self.assertEqual(project.text, result.text)

    def test_an_unknown_state_cannot_silently_look_accepted(self):
        with self.assertRaises(KeyError):
            state_badge('Новый результат', 'done-by-task-count')

    def test_error_and_success_keep_their_announcement_roles(self):
        self.assertEqual(Markup(notice('Сохранено')).elements[0][1]['role'], 'status')
        self.assertEqual(Markup(notice('Не удалось сохранить', error=True)).elements[0][1]['role'], 'alert')
