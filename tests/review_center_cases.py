"""Review-center regressions shared by the isolated SQLite/PostgreSQL suites."""
from contextlib import closing
from html import unescape
from html.parser import HTMLParser
import re
import time
from urllib.parse import parse_qs, urlsplit


class CenterForms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.current, self.textareas, self.textarea = [], None, [], None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = dict(method=attrs.get('method', 'get').lower(), inputs={}, action=attrs.get('action', ''))
            self.forms.append(self.current)
        if tag == 'input' and self.current is not None:
            self.current['inputs'][attrs.get('name')] = attrs.get('value', '')
        if tag == 'textarea':
            self.textarea = dict(name=attrs.get('name'), text='')
            self.textareas.append(self.textarea)

    def handle_data(self, value):
        if self.textarea is not None:
            self.textarea['text'] += value

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None
        if tag == 'textarea':
            self.textarea = None


class ReviewCenterCases:
    @staticmethod
    def center_work_keys(page):
        return re.findall(r'data-work-key="([^"]+)"', page)

    @staticmethod
    def center_decision_forms(page):
        return [form for form in CenterForms(page).forms
                if form['method'] == 'post' and 'revision' in form['inputs']]

    def center_decide(self, work='stage-1-1', revision=1, action='accept', comment='', suffix=''):
        return self.post('/teacher/review?work=' + work + suffix, data=dict(
            number='1', revision=str(revision), action=action, teacher_comment=comment))

    def test_center_roles_scope_and_get_are_read_only(self):
        self.seed_review_queue()
        self.sign_in(1)
        before = self.database_snapshot()
        for path in ('/teacher/review', '/teacher/review?work=stage-2-1',
                     '/teacher/review?work=defense-1', '/teacher/review?kind=report&work=report-1-11'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertNotIn('Private stage', response.text)
            self.assertNotIn('Private report', response.text)
        self.assertEqual(self.database_snapshot(), before)
        for path in ('/teacher/review?project=3', '/teacher/review?work=stage-3-1',
                     '/teacher/review?work=defense-3', '/teacher/review?kind=report&work=report-3-12'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, path)
            self.assertNotIn('Private stage', response.text)
        self.sign_in(3)
        self.assertEqual(self.client.get('/teacher/review').status_code, 403)
        self.assertEqual(self.center_decide().status_code, 403)
        self.assertEqual(self.module.app.test_client().get('/teacher/review').status_code, 302)

    def test_center_default_queue_excludes_reports_and_report_view_is_read_only(self):
        self.seed_review_queue()
        self.sign_in(1)
        page = self.client.get('/teacher/review').text
        self.assertIn('stage-2-1', self.center_work_keys(page))
        self.assertIn('defense-1', self.center_work_keys(page))
        self.assertFalse(any(key.startswith('report-') for key in self.center_work_keys(page)))
        page = self.client.get('/teacher/review?kind=report&work=report-1-11').text
        self.assertIn('&lt;script&gt;Отчёт команды&lt;/script&gt;', page)
        self.assertNotIn('<script>Отчёт команды</script>', page)
        self.assertNotIn('Old overridden report', page)
        self.assertEqual(self.center_decision_forms(page), [])
        before = self.database_snapshot()
        self.assertEqual(self.center_decide('report-1-11').status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_center_filters_search_selection_and_invalid_keys(self):
        self.seed_review_queue()
        self.sign_in(1)
        page = self.client.get('/teacher/review?kind=stage&project=2&q=STUDENT&sort=newest').text
        self.assertIn('stage-2-1', self.center_work_keys(page))
        self.assertNotIn('defense-1', self.center_work_keys(page))
        self.assertIn('id="review-detail"', page)
        page = self.client.get('/teacher/review?kind=report&q=КОМАНДНЫЙ').text
        self.assertIn('report-1-11', self.center_work_keys(page))
        self.assertNotIn('report-1-10', self.center_work_keys(page))
        for raw in ('garbage', 'stage-1-6', '<script>', 'report-one-1'):
            self.assertEqual(self.client.get('/teacher/review?work=' + raw).status_code, 400, raw)
        for raw in ('bad', '0', str(2**64)):
            self.assertEqual(self.client.get('/teacher/review?project=' + raw).status_code, 400, raw)

    def test_center_stage_decisions_keep_history_and_personal_scores(self):
        self.sign_in(1)
        self.assertEqual(self.post('/project/1/assessment', data=self.assessment_data()).status_code, 302)
        with closing(self.module.get_db()) as conn:
            grades = [tuple(row) for row in conn.execute('SELECT * FROM project_assessments')]
        self.assertEqual(self.history_stage(result='Первая сдача центра').status_code, 302)
        self.sign_in(1)
        response = self.center_decide(action='return', comment='Добавьте сравнение')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.location).path, '/teacher/review')
        self.assertEqual(self.stage_rows()[0]['state'], 'progress')
        self.assertEqual(self.history_stage(revision=2, result='Исправленная сдача центра').status_code, 302)
        self.sign_in(1)
        page = self.client.get('/teacher/review?work=stage-1-1').text
        self.assertIn('Исправленная сдача центра', page)
        self.assertIn('Первая сдача центра', page)
        self.assertIn('Добавьте сравнение', page)
        self.assertTrue(all(area['text'] == '' for area in CenterForms(page).textareas
                            if area['name'] == 'teacher_comment'))
        response = self.center_decide(revision=3, comment='Теперь всё обосновано', suffix='&kind=stage&project=1&sort=newest')
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.location).query)
        self.assertNotIn('work', query)
        self.assertEqual(query.get('kind'), ['stage'])
        self.assertEqual(query.get('project'), ['1'])
        self.assertEqual(query.get('sort'), ['newest'])
        rows = self.stage_rows()
        self.assertEqual([row['state'] for row in rows], ['done', 'todo', 'todo', 'todo', 'todo'])
        self.assertEqual([row['event'] for row in self.history_rows()], ['submit', 'return', 'submit', 'accept'])
        with closing(self.module.get_db()) as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM project_assessments')], grades)

    def test_center_defense_return_resubmit_accept_does_not_accept_stages(self):
        self.assertEqual(self.defense_post(description='Первое видео').status_code, 302)
        self.sign_in(1)
        self.assertEqual(self.center_decide('defense-1', action='return', comment='Проверьте звук').status_code, 302)
        self.assertEqual(self.defense_post(revision=2, description='Видео с новым звуком', url='https://example.com/new').status_code, 302)
        self.sign_in(1)
        page = self.client.get('/teacher/review?work=defense-1').text
        for text in ('Первое видео', 'Видео с новым звуком', 'Проверьте звук', 'https://example.com/new'):
            self.assertIn(text, page)
        self.assertEqual(self.center_decide('defense-1', revision=3, comment='Звук исправлен').status_code, 302)
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute('SELECT state FROM project_defenses WHERE project_id=1').fetchone()['state'], 'accepted')
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM project_assessments').fetchone()['n'], 0)
        self.assertEqual([row['state'] for row in self.stage_rows()], ['todo'] * 5)

    def test_center_validation_keeps_typed_comment_without_writing(self):
        self.history_stage(result='Работа для возврата')
        self.sign_in(1)
        before = self.database_snapshot()
        for comment in (' ', '<b>' + 'x' * 2001 + '</b>'):
            response = self.center_decide(action='return', comment=comment)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.database_snapshot(), before)
            self.assertTrue(any(area['name'] == 'teacher_comment' and area['text'] == comment
                                for area in CenterForms(response.text).textareas))
            self.assertNotIn('<b>' + 'x' * 2001, response.text)

    def test_center_stale_decision_does_not_reapply_comment_to_a_new_attempt(self):
        self.history_stage(result='Отправка один')
        self.history_stage(user=1, revision=1, action='return', teacher_comment='Переработайте')
        self.history_stage(revision=2, result='Отправка два')
        self.sign_in(1)
        before = self.database_snapshot()
        response = self.center_decide(revision=1, comment='<b>Старая вкладка учителя</b>')
        self.assertEqual(response.status_code, 400)
        self.assertIn('&lt;b&gt;Старая вкладка учителя&lt;/b&gt;', response.text)
        self.assertIn('Отправка два', response.text)
        self.assertEqual(self.database_snapshot(), before)
        self.assertFalse(any(area['text'] == '<b>Старая вкладка учителя</b>'
                             for area in CenterForms(response.text).textareas if area['name'] == 'teacher_comment'))

    def test_center_work_already_reviewed_has_no_decision_form_and_preserves_unsaved_comment(self):
        self.history_stage(result='Сдано')
        self.history_stage(user=1, revision=1, action='accept')
        self.sign_in(1)
        before = self.database_snapshot()
        page = self.client.get('/teacher/review?work=stage-1-1')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.center_decision_forms(page.text), [])
        response = self.center_decide(revision=1, comment='Несохранённое замечание')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Несохранённое замечание', response.text)
        self.assertEqual(self.center_decision_forms(response.text), [])
        self.assertEqual(self.database_snapshot(), before)

    def test_center_csrf_and_revoked_membership_reject_writes(self):
        self.sign_in(3)
        self.assertEqual(self.post('/project/2/stages', data=dict(number='1', revision='0', action='submit', result='Доступная работа')).status_code, 302)
        self.sign_in(1)
        before = self.database_snapshot()
        for token in (None, 'invalid-token'):
            data = dict(number='1', revision='1', action='accept', teacher_comment='Не должно сохраниться')
            if token is not None:
                data['csrf_token'] = token
            self.assertEqual(self.client.post('/teacher/review?work=stage-2-1', data=data).status_code, 400)
            self.assertEqual(self.database_snapshot(), before)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
            conn.commit()
        before = self.database_snapshot()
        self.assertEqual(self.client.get('/teacher/review?work=stage-2-1').status_code, 403)
        self.assertEqual(self.center_decide('stage-2-1').status_code, 403)
        self.assertEqual(self.database_snapshot(), before)

    def test_center_comparison_does_not_treat_a_legacy_draft_as_a_previous_submission(self):
        self.history_stage(action='save', result='Несданный старый черновик')
        self.history_stage(revision=1, result='Первая настоящая сдача')
        self.sign_in(1)
        page = self.client.get('/teacher/review?work=stage-1-1').text
        self.assertIn('Первая настоящая сдача', page)
        self.assertNotIn('Несданный старый черновик', page)
        self.assertEqual(len(self.center_decision_forms(page)), 1)

    def test_center_previous_submission_ignores_other_work_and_intermediate_drafts(self):
        self.history_stage(result='Исходная версия выбранного этапа')
        self.history_stage(user=1, revision=1, action='return', teacher_comment='Уточните эксперимент')
        self.history_stage(revision=2, action='save', result='Промежуточный несданный текст')
        self.defense_post(description='Другая работа — видеозащита')
        self.defense_post(user_id=1, revision=1, action='return', comment='Замечание только к видео')
        self.history_stage(revision=3, result='Новая версия выбранного этапа')
        self.sign_in(1)
        page = self.client.get('/teacher/review?work=stage-1-1').text
        self.assertIn('Исходная версия выбранного этапа', page)
        self.assertIn('Новая версия выбранного этапа', page)
        self.assertIn('Уточните эксперимент', page)
        self.assertNotIn('Промежуточный несданный текст', page)
        self.assertNotIn('Другая работа — видеозащита', page)
        self.assertNotIn('Замечание только к видео', page)

    def test_center_order_uses_submission_time_not_last_record_update(self):
        self.history_stage(result='Ранняя сдача')
        self.history_stage(project=2, result='Поздняя сдача')
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE work_history SET recorded_at='2026-01-01 12:00:00' WHERE project_id=1 AND event='submit'")
            conn.execute("UPDATE work_history SET recorded_at='2026-02-01 12:00:00' WHERE project_id=2 AND event='submit'")
            conn.execute("UPDATE project_stages SET updated_at='2026-03-01 12:00:00' WHERE project_id=1")
            conn.execute("UPDATE project_stages SET updated_at='2026-01-01 12:00:00' WHERE project_id=2")
            conn.commit()
        self.sign_in(1)
        for sort, expected in (('oldest', ['stage-1-1', 'stage-2-1']), ('newest', ['stage-2-1', 'stage-1-1'])):
            page = self.client.get('/teacher/review?kind=stage&sort=' + sort).text
            self.assertEqual(self.center_work_keys(page), expected)

    def test_center_reports_pagination_preserves_filter_and_latest_report(self):
        self.seed_review_queue()
        with closing(self.module.get_db()) as conn:
            conn.executemany('INSERT INTO tasks(id,project_id,title,assignee_id) VALUES(?,2,?,3)',
                             [(100 + n, f'Center task {n}') for n in range(45)])
            conn.executemany('INSERT INTO task_updates(id,task_id,user_id,body,created_at) VALUES(?,?,3,?,?)',
                             [(100 + n, 100 + n, f'Center report {n}', '2026-01-01 12:00:00') for n in range(45)])
            conn.execute("INSERT INTO task_updates(id,task_id,user_id,body,created_at) VALUES(1000,100,3,'New replacement','2026-02-01 12:00:00')")
            conn.commit()
        self.sign_in(1)
        page = self.client.get('/teacher/review?kind=report&project=2&q=CENTER&sort=newest&page=2').text
        hrefs = re.findall(r'href="([^"]+)"', page)
        pagination = [parse_qs(urlsplit(unescape(href)).query) for href in hrefs
                      if urlsplit(unescape(href)).path == '/teacher/review' and 'page=' in unescape(href)]
        self.assertTrue(any(query.get('page') == ['3'] for query in pagination))
        for query in pagination:
            self.assertEqual(query.get('kind'), ['report'])
            self.assertEqual(query.get('project'), ['2'])
            self.assertEqual(query.get('q'), ['CENTER'])
            self.assertEqual(query.get('sort'), ['newest'])
        self.assertNotIn('report-2-100', self.center_work_keys(page))
        self.assertFalse(any(key.startswith('report-3-') for key in self.center_work_keys(page)))

    def test_center_supervision_preview_does_not_open_results_or_history(self):
        pid = self.supervision_empty_project()
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision) VALUES(50,1,'review','Скрытый результат будущего подопечного',3,1)")
            conn.execute("INSERT INTO work_history(project_id,kind,number,revision,event,attempt,state,result) VALUES(50,'stage',1,1,'submit',1,'review','Скрытая история будущего подопечного')")
            conn.commit()
        code = self.supervision_code(teacher=2)
        self.assertEqual(self.supervision_request(code, pid).status_code, 302)
        request_id = self.supervision_row()['id']
        self.sign_in(2)
        before = self.database_snapshot()
        page = self.client.get('/teacher/review').text
        self.assertIn('Consent-only project', page)
        self.assertIn('student-one', page)
        self.assertIn(f'/supervision#request-{request_id}', page)
        self.assertNotIn('Скрытый результат будущего подопечного', page)
        self.assertNotIn('Скрытая история будущего подопечного', page)
        self.assertNotIn('stage-50-1', self.center_work_keys(page))
        self.assertEqual(self.client.get('/teacher/review?work=stage-50-1').status_code, 403)
        self.assertEqual(self.center_decide('stage-50-1').status_code, 403)
        self.assertEqual(self.database_snapshot(), before)
        with closing(self.module.get_db()) as conn:
            conn.execute('UPDATE supervision_requests SET expires=0 WHERE id=?', (request_id,))
            conn.commit()
        self.assertNotIn(f'/supervision#request-{request_id}', self.client.get('/teacher/review').text)

    def test_center_supervision_actions_wait_for_own_consent_and_respect_hub_page(self):
        code = self.supervision_code(teacher=2)
        self.assertEqual(self.supervision_request(code, project=2).status_code, 302)
        request_id = self.supervision_row()['id']
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.assertEqual(self.supervision_row()['state'], 'pending')
        self.assertNotIn(f'/supervision#request-{request_id}', self.client.get('/teacher/review').text)
        self.sign_in(1)
        self.assertIn(f'/supervision#request-{request_id}', self.client.get('/teacher/review').text)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM supervision_requests')
            conn.executemany('INSERT INTO projects(id,name,owner_id) VALUES(?,?,3)',
                             [(200 + n, f'Pending center request {n}') for n in range(31)])
            conn.executemany("""INSERT INTO supervision_requests(id,project_id,owner_id,target_id,kind,state,
                previous_json,approvals_json,owner_approved,expires) VALUES(?,?,3,2,'change','pending','[1]',?,1,?)""",
                             [(1000 + n, 200 + n, '[]' if n == 0 else '[2]', int(time.time()) + 86400)
                              for n in range(31)])
            conn.commit()
        self.sign_in(2)
        page = self.client.get('/teacher/review').text
        self.assertNotIn('/supervision#request-1000', page)
        self.assertNotIn('Pending center request 0', page)
        self.assertIn('href="/supervision"', page)
        self.assertIn('Показана часть', page)

    def test_center_forged_number_cannot_decide_a_different_stage(self):
        self.history_stage(result='Согласованная тема')
        self.history_stage(user=1, revision=1, action='accept')
        self.history_stage(number=2, result='Работа второго этапа')
        self.sign_in(1)
        before = self.database_snapshot()
        response = self.post('/teacher/review?work=stage-1-1', data=dict(
            number='2', revision='1', action='accept', teacher_comment='Подмена номера'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        response = self.post('/teacher/review?work=stage-1-2', data=dict(
            number='1', revision='1', action='accept', teacher_comment='Другая подмена'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.stage_rows()[1]['state'], 'review')
