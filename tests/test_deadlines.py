"""Calendar boundaries without any database or local-device timezone dependency."""
import unittest
from datetime import date, datetime, timedelta, timezone
from deadlines import business_today, deadline_info, deadline_text, parse_deadline, urgent_tasks


class DeadlineTests(unittest.TestCase):
    def test_moscow_midnight_independent_of_clock_timezone(self):
        before = datetime(2026, 12, 31, 20, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=1)
        self.assertEqual(business_today(before), date(2026, 12, 31))
        self.assertEqual(business_today(after), date(2027, 1, 1))
        self.assertEqual(business_today(after.astimezone(timezone(timedelta(hours=-8)))), date(2027, 1, 1))
        self.assertEqual(deadline_info('2026-12-31', today=business_today(before))['state'], 'today')
        self.assertEqual(deadline_info('2026-12-31', today=business_today(after))['state'], 'overdue')
        with self.assertRaises(ValueError):
            business_today(datetime(2026, 1, 1))

    def test_labels_and_exact_date_at_month_year_and_leap_boundaries(self):
        today = date(2028, 2, 28)
        for raw, label in [('2028-02-27', 'Просрочено на 1 день'), ('2028-02-28', 'Сегодня'),
                           ('2028-02-29', 'Завтра'), ('2028-03-01', 'Осталось 2 дня'),
                           ('2028-03-10', 'Осталось 11 дней'), ('2028-03-20', 'Осталось 21 день')]:
            with self.subTest(raw=raw):
                info = deadline_info(raw, today=today)
                self.assertEqual(info['label'], label)
                self.assertIn(info['display_date'], deadline_text(info))
        self.assertEqual(deadline_info('2027-01-01', today=date(2026, 12, 31))['label'], 'Завтра')
        self.assertEqual(deadline_info('0001-01-01', today=today)['display_date'], '01.01.0001')

    def test_missing_and_invalid_deadlines_are_not_synthesized(self):
        for raw in (None, '', 'wrong', '2026-02-30', '20260101', '2026-W01-1', '2026-1-1'):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_deadline(raw))
                info = deadline_info(raw)
                self.assertFalse(info['active'])
                self.assertEqual(info['css'], '')
                self.assertEqual(info['date'], '')

    def test_done_tasks_keep_exact_date_without_active_warning(self):
        info = deadline_info('2026-01-01', 'done', date(2026, 10, 13))
        self.assertEqual(deadline_text(info), 'Завершено · 01.01.2026')
        self.assertFalse(info['active'])
        self.assertEqual(info['css'], '')

    def test_urgent_tasks_sorted_and_completed_missing_invalid_excluded(self):
        tasks = [dict(id=n, title=str(n), deadline=raw, status=status) for n, raw, status in
                 [(1, '2026-10-16', 'todo'), (2, '2026-10-12', 'progress'), (3, None, 'todo'),
                  (4, '2026-01-01', 'done'), (5, 'bad', 'todo'), (6, '2026-10-17', 'todo')]]
        before = [task.copy() for task in tasks]
        self.assertEqual([t['id'] for t in urgent_tasks(tasks, date(2026, 10, 13))], [2, 1])
        self.assertEqual(tasks, before)
