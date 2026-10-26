"""Task board regressions shared by SQLite and isolated PostgreSQL."""
from contextlib import closing
from unittest.mock import patch


class TaskBoardCases:
    def board_task(self, task_id=3):
        with closing(self.module.get_db()) as conn:
            return dict(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def test_task_board_create_edit_and_legacy_forms_preserve_stage_description(self):
        self.sign_in(3)
        self.assertEqual(self.post('/project/2/add', data=dict(title='New documented task', description='Expected result',stage_number='5',assignee_id='3',return_to='/project/2?stage=5#task-board')).location, '/project/2?stage=5#task-board')
        with closing(self.module.get_db()) as conn:
            tid=conn.execute("SELECT id FROM tasks WHERE title='New documented task'").fetchone()[0]
        self.assertEqual((self.board_task(tid)['description'],self.board_task(tid)['stage_number']), ('Expected result',5))
        self.post(f'/project/2/edit/{tid}',data=dict(title='Edited title',assignee_id='3'))
        self.assertEqual((self.board_task(tid)['description'],self.board_task(tid)['stage_number']), ('Expected result',5))
        self.post(f'/project/2/edit/{tid}',data=dict(title='Edited title',description='New description',stage_number='2',assignee_id='3'))
        self.assertEqual((self.board_task(tid)['description'],self.board_task(tid)['stage_number']), ('New description',2))
        self.post(f'/project/2/edit/{tid}',data=dict(title='Edited title',description='',stage_number='',assignee_id='3'))
        self.assertEqual((self.board_task(tid)['description'],self.board_task(tid)['stage_number']), ('',None))

    def test_task_board_invalid_fields_cannot_write_and_create_preserves_input(self):
        self.sign_in(3)
        before=self.database_snapshot()
        for raw in ('0','6','1.0','01','-1','stage-2','9'*100):
            response=self.post('/project/2/add',data=dict(title='Keep my title',description='Keep my text',stage_number=raw,return_to='/project/2?mine=1&stage=2#task-board'))
            self.assertEqual(response.status_code,400)
            self.assertIn('Keep my text',response.text)
            self.assertIn('data-open-create="1"',response.text)
            self.assertIn('data-project-url="/project/2"',response.text)
            self.assertIn('name="mine" value="1"',response.text)
            self.assertEqual(self.database_snapshot(),before)
            self.post('/project/2/edit/3',data=dict(title='Denied edit',stage_number=raw))
            self.assertEqual(self.database_snapshot(),before)
        response=self.post('/project/2/add',data=dict(title='Keep title',description='x'*3001))
        self.assertEqual(response.status_code,400)
        self.post('/project/2/edit/3',data=dict(description='x'*3001))
        self.assertEqual(self.database_snapshot(),before)

    def test_task_board_stage_and_assignee_filters_are_scoped_and_read_only(self):
        with closing(self.module.get_db()) as conn:
            conn.executemany('INSERT INTO tasks(id,project_id,title,stage_number,assignee_id) VALUES(?,?,?,?,?)',[
                (21,2,'Stage two assigned',2,3),(22,2,'Stage five unassigned',5,None),(23,3,'Foreign task secret',2,2)])
            conn.commit()
        self.sign_in(3)
        before=self.database_snapshot()
        self.assertEqual(self.board_ids(self.client.get('/project/2?stage=2&assignee=3').text),[21])
        self.assertEqual(self.board_ids(self.client.get('/project/2?stage=5&assignee=none').text),[22])
        self.assertEqual(self.board_ids(self.client.get('/project/2?stage=none').text),[3])
        plain=self.client.get('/project/2').text
        for args in ('stage=6','assignee=2','column=invalid'):
            self.assertEqual(self.client.get('/project/2?'+args).text,plain)
        self.assertNotIn('Foreign task secret',plain)
        self.assertEqual(self.database_snapshot(),before)

    def test_task_board_roles_csrf_and_foreign_task_edits_are_denied(self):
        self.add_candidate()
        with closing(self.module.get_db()) as conn:
            conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,4)')
            conn.commit()
        for user in (1,2,4):
            self.sign_in(user)
            before=self.database_snapshot()
            for path in ('/project/2/add','/project/2/edit/3'):
                self.assertEqual(self.post(path,data=dict(title='Denied',description='Denied',stage_number='5')).status_code,403)
            self.assertEqual(self.database_snapshot(),before)
        self.sign_in(3)
        before=self.database_snapshot()
        self.assertEqual(self.client.post('/project/2/add',data=dict(title='No token',stage_number='5')).status_code,400)
        self.assertEqual(self.client.post('/project/2/edit/3',data=dict(description='No token')).status_code,400)
        self.post('/project/2/edit/1',data=dict(title='Foreign id',description='Denied',stage_number='5'))
        self.assertEqual(self.database_snapshot(),before)

    def test_task_board_task_completion_never_accepts_stage_or_changes_history(self):
        self.sign_in(3)
        self.post('/project/2/edit/3',data=dict(title='Task for documentation',description='Ready',stage_number='5',assignee_id='3'))
        with closing(self.module.get_db()) as conn:
            before=[tuple(r) for r in conn.execute('SELECT * FROM project_stages ORDER BY project_id,number')]
        events=self.history_rows(project=2)
        self.assertEqual(self.post('/project/2/status',json=dict(task_id=3,status='done')).status_code,200)
        with closing(self.module.get_db()) as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM project_stages ORDER BY project_id,number')],before)
        self.assertEqual(self.history_rows(project=2),events)
        self.assertEqual(self.board_task()['stage_number'],5)

    def test_task_board_reports_filters_and_safe_returns_survive_form_posts(self):
        from task_board import board_return
        self.sign_in(3)
        target='/project/2?mine=1&stage=3&column=progress#task-3'
        self.assertEqual(self.post('/project/2/task/3/update',data=dict(body='Report stays',return_to=target)).location,target)
        self.assertEqual(self.post('/project/2/edit/3',data=dict(title='Updated',stage_number='3',assignee_id='3',return_to=target)).location,target)
        self.assertEqual(self.post('/project/2/delete/3',data=dict(return_to='/project/2?mine=1#task-board')).location,'/project/2?mine=1#task-board')
        self.assertIsNotNone(self.board_task())
        for url in ('https://example.com','//example.com','http://[invalid','/project/3','\\\\evil','\n/project/2'):
            self.assertEqual(board_return(2,url),'/project/2#task-board')
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute('SELECT body FROM task_updates WHERE task_id=3').fetchone()[0],'Report stays')

    def test_task_board_escaped_content_and_initialization_keep_existing_data(self):
        from task_board import initialize_task_board
        self.sign_in(3)
        self.post('/project/2/edit/3',data=dict(title='<b>Title</b>',description='<script>alert(1)</script>',stage_number='4',assignee_id='3'))
        before=self.database_snapshot()
        with closing(self.module.get_db()) as conn:
            initialize_task_board(conn); initialize_task_board(conn); conn.commit()
        page=self.client.get('/project/2').text
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',page)
        self.assertNotIn('<script>alert(1)</script>',page)
        self.assertIn('Этап 4 <svg',page)
        self.assertIn('class="board-disclosure task-details"',page)
        self.assertIn('/static/task-board.js',page)
        self.assertEqual(self.database_snapshot(),before)

    def test_task_board_owner_change_during_edit_is_rechecked_under_lock(self):
        self.sign_in(3)
        before=self.database_snapshot()
        # Simulate an out-of-date initial route check. The transaction remains authoritative.
        with patch.object(self.module,'is_project_owner',return_value=True):
            self.assertEqual(self.post('/project/1/add',data=dict(title='Forbidden',stage_number='2')).status_code,403)
            self.assertEqual(self.post('/project/1/edit/1',data=dict(title='Forbidden',stage_number='2')).status_code,403)
        self.assertEqual(self.database_snapshot(),before)
