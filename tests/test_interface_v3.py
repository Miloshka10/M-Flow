"""Real stage state and escaped project information in the accepted interface."""
from html.parser import HTMLParser
import unittest

from design_suite import compact_stage_route
from project_stages import stages_from_rows
from dashboard_view import student_dashboard
from teacher_queue import render_queue


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)
    def handle_starttag(self, name, attrs):
        self.tags.append((name, dict(attrs)))


class InterfaceV3Tests(unittest.TestCase):
    def test_stage_route_uses_acceptance_not_task_completion(self):
        stages = stages_from_rows([dict(number=1, state='done'),
                                  dict(number=2, state='progress', reviewed_by=7)])
        html = compact_stage_route(stages, 42)
        tags = Tags(html).tags
        links = [attrs['href'] for name, attrs in tags if name == 'a']
        self.assertEqual(len(links), 5)
        self.assertTrue(all(url.startswith('/project/42/stages?view_stage=') for url in links))
        current = [attrs for name, attrs in tags if attrs.get('aria-current') == 'step']
        self.assertEqual(len(current), 1)
        self.assertIn('Принято 1 из 5 этапов', html)
        self.assertIn('Нужна доработка', html)
        self.assertEqual(sum(name == 'li' and attrs.get('class') == 'done' for name, attrs in tags), 1)

    def test_all_accepted_stages_do_not_invent_a_current_stage(self):
        html = compact_stage_route(stages_from_rows([dict(number=n, state='done') for n in range(1, 6)]), 3)
        self.assertIn('Принято 5 из 5 этапов', html)
        self.assertNotIn('aria-current="step"', html)

    def test_teacher_review_link_opens_focused_result(self):
        item = dict(kind='stage', requires_decision=True, date=None, author='student',
                    team='', body='Result', key='stage-42-1', project_id=42, project_name='Project',
                    title='Stage 1', work='stage-1', action='Проверить этап')
        html = render_queue(dict(total=1, decisions=1, items=[item], page=1, pages=1), {})
        self.assertIn('href="/teacher/review/42/stage-1"', html)

    def test_dashboard_keeps_feedback_private_escaped_and_not_duplicated(self):
        project = dict(id=1, name='<script>project</script>', total=4, done=4, overdue=0, deadlines=[])
        stages = stages_from_rows([dict(number=1, state='progress', reviewed_by=7)])
        comment = '<img src=x onerror=alert(1)>'
        step = dict(key='revise', target='stages#stage-1', title='Доработайте результат',
                    text='Исправьте замечание.', action='Перейти к доработке')
        html = student_dashboard(dict(username='student'), [project], 'csrf', '', '',
                                 focus=dict(project=project, stages=stages, step=step, comment=comment, reviewer='<teacher>'),
                                 project_states={1:dict(label='Нужна доработка', tone='revision', stages_done=0)})
        self.assertNotIn(comment, html)
        self.assertNotIn('<script>project</script>', html)
        self.assertEqual(html.count('&lt;img src=x onerror=alert(1)&gt;'), 1)
        self.assertIn('&lt;teacher&gt;', html)
        self.assertIn('Принято 0 из 5 этапов', html)
        self.assertIn('data-home-step="revise"', html)
        self.assertIn('href="/project/1/stages#stage-1-result"', html)
        self.assertNotIn('preview-tools', html)


if __name__ == '__main__':
    unittest.main()
