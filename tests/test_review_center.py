from datetime import datetime, timezone
import unittest

from review_center import wait_label, work_project


class ReviewCenterUnits(unittest.TestCase):
    def test_wait_is_elapsed_from_submission_and_not_a_student_deadline(self):
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.assertEqual(wait_label('2026-10-29T15:00:00+03:00', now), 'Ожидает 1 дн.')
        self.assertEqual(wait_label('2026-10-30 11:00:00', now), 'Ожидает меньше суток')
        self.assertEqual(wait_label('9999-12-31 23:59:59', now), 'Проверьте дату отправки')
        for raw in (None, '', 'not-a-date', '2026-02-30'):
            self.assertEqual(wait_label(raw, now), 'Дата отправки неизвестна')

    def test_work_key_does_not_accept_other_routes_or_an_unbounded_id(self):
        self.assertEqual(work_project('stage-10-5'), 10)
        self.assertEqual(work_project('defense-1'), 1)
        self.assertEqual(work_project('report-2-19'), 2)
        for raw in ('stage-1-0', 'stage-1-6', 'stage-1-1\n', '/project/1', 'stage-' + '1'*30 + '-1'):
            with self.assertRaises(ValueError):
                work_project(raw)
