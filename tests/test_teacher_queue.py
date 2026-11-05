from datetime import datetime, timezone
import unittest

from teacher_queue import sent_date, queue_page, render_queue


class QueueDateTests(unittest.TestCase):
    def test_empty_material_is_omitted_and_only_a_decision_gets_the_primary_action(self):
        project = dict(id=2, name='Project', students=[], current_stage=dict(number=1, title='Topic',
                       state='review', revision=1, updated_at='2026-11-06 09:00:00'), defense={}, reports=[])
        html = render_queue(queue_page([project]), {})
        self.assertNotIn('ds-queue-material', html)
        self.assertIn('class="btn" href="/project/2/stages#stage-1-result"', html)
        project['current_stage'] = None
        project['reports'] = [dict(task_title='Task', task_id=10, report=dict(id=20, username='Student',
                              created_at='2026-11-06 09:00:00', body='Report <script>'))]
        html = render_queue(queue_page([project]), {})
        self.assertNotIn('class="btn"', html)
        self.assertIn('Отчёт и команда', html)
        self.assertIn('Report &lt;script&gt;', html)
        self.assertIn('решение не требуется', html)

    def test_utc_and_offset_dates_sort_as_same_instant(self):
        self.assertEqual(sent_date('2026-01-01 12:00:00'), sent_date('2026-01-01T15:00:00+03:00'))
        self.assertEqual(sent_date('2026-01-01T12:00:00Z'), datetime(2026,1,1,12,tzinfo=timezone.utc))
        for value in ('',None,'bad','2026-02-30'):
            self.assertIsNone(sent_date(value))

    def test_unknown_dates_last_in_both_orders_and_stable_ties(self):
        projects = [dict(id=n,name='Project',students=[],current_stage=dict(number=1,title='Topic',state='review',revision=1,
            updated_at=sent),defense={},reports=[]) for n,sent in ((2,''),(3,'2026-01-01 12:00:00'),(1,'2026-01-01 12:00:00'),(4,'2026-02-01 12:00:00'))]
        for sort, expected in (('oldest',[1,3,4,2]),('newest',[4,1,3,2])):
            items = queue_page(projects, sort=sort)['items']
            self.assertEqual([i['project_id'] for i in items], expected)
        self.assertEqual(queue_page(projects, page=999999)['page'], 1)
