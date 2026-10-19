"""History regressions shared by the SQLite and isolated PostgreSQL suites."""
from contextlib import closing
from unittest.mock import patch


class WorkHistoryCases:
    def history_rows(self, project=1):
        with closing(self.module.get_db()) as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM work_history WHERE project_id=? ORDER BY id', (project,))]

    def history_stage(self, user=3, revision=0, action='submit', number=1, project=1, **fields):
        self.sign_in(user)
        return self.post(f'/project/{project}/stages', data=dict(
            number=str(number), revision=str(revision), action=action, **fields))

    def test_history_stage_drafts_resubmission_and_decisions_keep_exact_versions(self):
        for revision in range(2):
            self.assertEqual(self.history_stage(revision=revision, action='save', result=f'Draft {revision}').status_code, 302)
        self.assertEqual(self.history_rows(), [])
        self.assertEqual(self.history_stage(revision=2, result='First submission').status_code, 302)
        self.assertEqual(self.history_stage(user=1, revision=3, action='return', teacher_comment='Explain the goal').status_code, 302)
        self.assertEqual(self.history_stage(revision=4, action='save', result='Edited draft').status_code, 302)
        self.assertEqual(self.history_stage(revision=5, result='Second submission').status_code, 302)
        self.assertEqual(self.history_stage(user=1, revision=6, action='accept', teacher_comment='Now clear').status_code, 302)
        rows = self.history_rows()
        self.assertEqual([(r['event'], r['attempt'], r['revision']) for r in rows],
                         [('submit',1,3), ('return',1,4), ('submit',2,6), ('accept',2,7)])
        self.assertEqual([r['result'] for r in rows], ['First submission'] * 2 + ['Second submission'] * 2)
        self.assertEqual([r['comment'] for r in rows], ['', 'Explain the goal', '', 'Now clear'])
        self.assertEqual([r['actor_name'] for r in rows], ['student-one', 'teacher-one'] * 2)
        self.assertEqual(rows[2]['reviewer_id'], None)
        self.assertTrue(all(r['recorded_at'] for r in rows))
        page = self.client.get('/project/1/history').text
        self.assertIn('Отправка №2', page)
        self.assertNotIn('Edited draft', page)
        self.assertNotIn('Состояние до включения истории</h2>', page)

    def test_history_documentation_and_defense_keep_original_links(self):
        with closing(self.module.get_db()) as conn:
            conn.executemany("INSERT INTO project_stages(project_id,number,state,result) VALUES(1,?,'done','Old stage')", [(n,) for n in range(1,5)])
            conn.commit()
        self.history_stage(number=5, result='Documentation v1', presentation_url='https://example.com/slides-one')
        self.history_stage(user=1, number=5, revision=1, action='return', teacher_comment='Add sources')
        self.history_stage(number=5, revision=2, result='Documentation v2', presentation_url='https://example.com/slides-two')
        self.history_stage(user=1, number=5, revision=3, action='accept')
        self.assertEqual(self.defense_post(description='Speech one', url='https://example.com/video-one').status_code, 302)
        self.defense_post(user_id=1, revision=1, action='return', comment='Improve sound')
        self.defense_post(revision=2, action='save', description='Draft speech', url='https://example.com/draft')
        self.defense_post(revision=3, description='Speech two', url='https://example.com/video-two')
        self.defense_post(user_id=1, revision=4, action='accept')
        rows = self.history_rows()
        self.assertEqual(len(rows), 8)
        self.assertEqual([r['resource_url'] for r in rows],
            ['https://example.com/slides-one'] * 2 + ['https://example.com/slides-two'] * 2 +
            ['https://example.com/video-one'] * 2 + ['https://example.com/video-two'] * 2)
        self.assertEqual([r['attempt'] for r in rows], [1,1,2,2] * 2)
        page = self.client.get('/project/1/history?work=defense').text
        self.assertNotIn('Documentation v1', page)
        self.assertNotIn('Draft speech', page)
        self.assertIn('Speech one', page)
        self.assertIn('Improve sound', page)
        self.assertIn('Принято без комментария.', page)
        self.assertIn('/project/1/history?work=stage-5', self.client.get('/project/1/stages').text)
        self.assertIn('/project/1/history?work=defense', self.client.get('/project/1/defense').text)
        self.assertIn('/project/1/history', self.client.get('/project/1').text)

    def test_history_legacy_read_is_honest_and_first_change_preserves_baseline(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("""INSERT INTO project_stages(project_id,number,state,result,teacher_comment,submitted_by,reviewed_by,revision,updated_at)
                VALUES(1,1,'progress','Old edited draft','Old return',3,1,4,'2026-10-18 10:00:00')""")
            conn.execute("""INSERT INTO project_defenses(project_id,description,state,revision)
                VALUES(1,'Old defense draft','draft',2)""")
            conn.commit()
        self.sign_in(3)
        before = self.database_snapshot()
        from work_history import initialize_history
        with closing(self.module.get_db()) as conn:
            initialize_history(conn)
            initialize_history(conn)
            conn.commit()
        for _ in range(2):
            page = self.client.get('/project/1/history').text
            self.assertIn('Old edited draft', page)
            self.assertIn('Точная дата отправки и содержание прошлых попыток неизвестны', page)
            self.assertIn('18.10.2026 13:00 МСК', page)
            self.assertEqual(page.count('data-history-event="legacy"'), 2)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.history_stage(revision=4, action='save', result='New draft').status_code, 302)
        self.history_stage(revision=5, result='First tracked submission')
        rows = self.history_rows()
        self.assertEqual([(r['event'],r['attempt'],r['revision']) for r in rows], [('legacy',0,4), ('submit',1,6)])
        self.assertEqual(rows[0]['result'], 'Old edited draft')
        self.assertEqual(rows[0]['reviewer_name'], 'teacher-one')
        self.assertEqual(rows[0]['source_updated_at'], '2026-10-18 10:00:00')
        self.assertEqual(self.client.get('/project/1/history').text.count('data-history-event="legacy"'), 2)
        self.defense_post(revision=2, description='New speech')
        self.assertEqual([r['event'] for r in self.history_rows() if r['kind']=='defense'], ['legacy', 'submit'])

    def test_history_stale_invalid_csrf_and_failed_transactions_write_nothing(self):
        self.sign_in(3)
        before = self.database_snapshot()
        for path, data in (('/project/1/stages',dict(number='1',revision='0',action='submit',result='')),
                           ('/project/1/defense',dict(revision='0',action='submit',video_url='javascript:alert(1)'))):
            self.assertEqual(self.post(path, data=dict(data)).status_code, 400)
            data['result'] = 'Valid result'
            self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        for module, call in (('project_stages', lambda: self.history_stage(result='Rollback stage')),
                             ('video_defense', lambda: self.defense_post())):
            with patch(module + '.record_change', side_effect=RuntimeError('Synthetic history failure')):
                with self.assertRaises(RuntimeError):
                    call()
            self.assertEqual(self.database_snapshot(), before)
            with patch(module + '.notify_project', side_effect=RuntimeError('Synthetic notification failure')):
                with self.assertRaises(RuntimeError):
                    call()
            self.assertEqual(self.database_snapshot(), before)
        self.history_stage(result='Only once')
        before = self.database_snapshot()
        self.assertEqual(self.history_stage(result='Duplicate').status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.defense_post()
        before = self.database_snapshot()
        self.assertEqual(self.defense_post().status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_history_access_role_changes_names_and_personal_grade_privacy(self):
        self.history_stage(project=2, result='Private submission')
        self.history_stage(user=1, project=2, revision=1, action='accept', teacher_comment='Original teacher decision')
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE users SET username='Renamed teacher' WHERE id=1")
            conn.commit()
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code).status_code, 302)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        self.seed_result_grade(project=2, note='PRIVATE-GRADE-SECRET')
        for uid, expected in ((1,403), (2,200), (3,200)):
            self.sign_in(uid)
            page = self.client.get('/project/2/history')
            self.assertEqual(page.status_code, expected)
            if expected == 200:
                self.assertIn('teacher-one', page.text)
                self.assertIn('Original teacher decision', page.text)
                self.assertNotIn('PRIVATE-GRADE-SECRET', page.text)
        self.add_candidate()
        self.sign_in(4)
        before = self.database_snapshot()
        for suffix in ('', '?work=stage-1', '?work=defense&page=2'):
            page = self.client.get('/project/2/history' + suffix)
            self.assertEqual(page.status_code, 403)
            self.assertNotIn('Private submission', page.text)
        self.assertEqual(self.database_snapshot(), before)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/project/2/history').status_code, 302)

    def test_history_pagination_filters_escaping_and_read_only_contract(self):
        from work_history import PAGE_SIZE
        self.history_stage(result='<script>unsafe()</script>')
        for index in range(7):
            self.history_stage(user=1, revision=2*index+1, action='return', teacher_comment=f'Fix {index}')
            self.history_stage(revision=2*index+2, result=f'Version {index}')
        self.defense_post(description='Defense only')
        self.sign_in(3)
        before = self.database_snapshot()
        for suffix, count in (('?work=stage-1',PAGE_SIZE), ('?work=stage-1&page=2',3), ('?work=stage-1&page=999',3), ('?work=stage-5',0)):
            page = self.client.get('/project/1/history' + suffix)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.text.count('data-history-event='), count)
            self.assertNotIn('Defense only', page.text)
        older = self.client.get('/project/1/history?work=stage-1&page=2').text
        self.assertIn('&lt;script&gt;unsafe()', older)
        self.assertNotIn('<script>unsafe()', older)
        for suffix in ('?work=unknown','?work=stage-9','?page=0','?page=-1','?page=abc','?page=999999999999'):
            self.assertEqual(self.client.get('/project/1/history' + suffix).status_code, 400)
        self.assertEqual(self.post('/project/1/history', data={'action':'delete'}).status_code, 405)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(len(self.history_rows()), 16)

    def test_history_invalid_legacy_links_are_not_executable(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("""INSERT INTO project_defenses(project_id,state,video_url,description)
                VALUES(1,'draft','javascript:alert(1)','<img src=x onerror=alert(1)>')""")
            conn.commit()
        self.sign_in(3)
        before = self.database_snapshot()
        page = self.client.get('/project/1/history').text
        self.assertIn('переход отключён', page)
        self.assertIn('&lt;img src=x', page)
        self.assertNotIn('href="javascript:', page)
        self.assertEqual(self.database_snapshot(), before)
