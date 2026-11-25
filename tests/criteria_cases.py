"""Versioned evidence and the defense passport on SQLite and PostgreSQL."""
from contextlib import closing
from io import BytesIO
import re
from unittest.mock import patch

from test_rich_attachment_text import text_pdf, workbook


class ProjectCriteriaCases:
    def test_project_criteria_from_return_to_saved_passport(self):
        pid = 2
        project = f'/project/{pid}'
        manage, passport = project + '/criteria', project + '/passport'
        center = '/teacher/review?work=stage-2-2'
        title = 'Есть замеры в трёх кабинетах'

        # A previously accepted work predates criteria and submission history.
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,result) VALUES(2,1,'done','Температура в школе')")
            conn.commit()
        self.sign_in(3)
        self.assertEqual(self.post(manage, data=dict(action='add', number='2', title=title)).status_code, 403)
        v1 = self.post(passport, data=dict(action='save')).location
        self.assertIn('Температура в школе', self.client.get(v1).text)
        self.assertEqual(self.post(passport, data=dict(action='save')).location, v1)

        self.sign_in(1)
        self.assertEqual(self.client.post(manage, data=dict(action='add', number='2', title=title)).status_code, 400)
        self.assertEqual(self.post(manage, data=dict(action='add', number='2', title=title)).status_code, 302)
        criteria = self.client.get(manage).text
        self.assertIn(title, criteria)
        cid = re.search(r'name="criterion_id" value="(\d+)"', criteria)[1]
        status_key, comment_key = f'criterion_status_{cid}', f'criterion_note_{cid}'
        evidence_note, evidence_file = f'evidence_note_{cid}', f'evidence_file_{cid}'
        early_pdf, late_pdf = text_pdf('Early data'), text_pdf('Room A, B and C')
        sheet = workbook('<row><c r="A1"><v>21</v></c></row>')

        self.assertEqual(self.history_stage(project=pid, number=2, action='save', result='Черновик',
                                           **{evidence_note: 'Сняли два показания'}).status_code, 302)
        draft = self.client.get(project + '/stages?view_stage=2').text
        self.assertIn('Сняли два показания', draft)
        failed = self.history_stage(project=pid, number=2, revision=1, result='Два показания',
                                    attachments=[(BytesIO(bytes(2 * 1024 * 1024 + 1)), 'large.pdf')],
                                    **{evidence_note: 'Введённое пояснение', evidence_file: '1'})
        self.assertEqual(failed.status_code, 400)
        self.assertIn('Файл «large.pdf» превышает 2 МБ.', failed.text)
        self.assertIn('Выберите заново для критерия: <strong>large.pdf</strong>', failed.text)
        self.assertIn('>Введённое пояснение</textarea>', failed.text)
        self.assertEqual(self.history_stage(project=pid, number=2, revision=1, result='Два показания',
                                          attachments=[(BytesIO(early_pdf), 'отчёт.pdf')],
                                          **{evidence_file: '3'}).status_code, 400)
        self.assertEqual(self.history_stage(project=pid, number=2, revision=1, result='Два показания',
                                          attachments=[(BytesIO(early_pdf), 'отчёт.pdf')],
                                          **{evidence_file: '1'}).status_code, 302)

        self.sign_in(1)
        page = self.client.get(center).text
        self.assertIn('Сняли два показания', page)
        first_file = re.search(r'/project/2/attachments/(\d+)">отчёт.pdf', page)[1]
        decision = dict(action='return', number='2', revision='2')
        self.assertEqual(self.post(center, data={**decision, status_key: 'revise', comment_key: ''}).status_code, 400)
        self.assertEqual(self.post(center, data={**decision, status_key: 'revise',
                                                comment_key: 'Добавьте третий кабинет <script>'}).status_code, 302)

        self.sign_in(3)
        overview = self.client.get(project).text
        self.assertIn('Добавьте третий кабинет &lt;script&gt;', overview)
        entry = self.history_rows(pid)[-1]['id']
        direct_remark = f'{project}/history?work=stage-2&entry={entry}'
        self.assertIn(f'entry={entry}#history-event-{entry}', self.client.get('/notifications').text)
        self.assertIn(f'entry={entry}#history-event-{entry}', overview)
        self.assertEqual(self.client.get(f'/project/1/history?entry={entry}').status_code, 404)
        self.assertNotIn('Два показания', self.client.get(passport).text)
        self.assertEqual(self.history_stage(project=pid, number=2, revision=3, result='Замеры трёх кабинетов',
                                           attachments=[(BytesIO(late_pdf), 'отчёт.pdf'), (BytesIO(sheet), 'замеры.xlsx')],
                                           **{evidence_note: 'Добавили кабинет C', evidence_file: '2'}).status_code, 302)

        self.sign_in(1)
        compared = self.client.get(center).text
        self.assertIn('Пояснение изменено · Файл изменён', compared)
        self.assertIn('Что было в предыдущей сдаче', compared)
        self.assertIn('Добавили кабинет C', compared)
        self.assertIn('Изменён', compared)
        second_file = re.search(r'/project/2/attachments/(\d+)">замеры.xlsx', compared)[1]
        accept = dict(action='accept', number='2', revision='4')
        self.assertEqual(self.post(center, data=accept).status_code, 400)
        forged = {**accept, status_key: 'confirmed', f'criterion_file_{cid}': first_file}
        self.assertEqual(self.post(center, data=forged).status_code, 400)
        self.assertEqual(self.post(center, data={**accept, status_key: 'confirmed'}).status_code, 302)
        self.assertIn('Подтверждено 1 из 1', self.client.get(project).text)
        accepted = self.client.get(passport).text
        self.assertIn(title, accepted)
        self.assertIn('Замеры трёх кабинетов', accepted)
        self.assertIn(f'/project/2/attachments/{second_file}', accepted)
        self.assertIn('Добавили кабинет C', accepted)
        self.assertNotIn(f'/project/2/attachments/{first_file}', accepted)
        self.assertEqual(self.post(passport, data=dict(action='save')).status_code, 403)

        self.sign_in(3)
        history = self.client.get(project + '/history?work=stage-2').text
        self.assertIn('Критерии и комментарии этой версии', history)
        self.assertIn('Добавьте третий кабинет &lt;script&gt;', history)
        remark = self.client.get(direct_remark).text
        self.assertIn(f'id="history-event-{entry}"', remark)
        self.assertIn('<details open><summary>Критерии и комментарии этой версии</summary>', remark)
        self.assertIn('Отправка №1', remark)
        self.assertNotIn('Добавили кабинет C', remark)
        self.assertEqual(self.client.get(f'{project}/attachments/{second_file}').data, sheet)
        v2 = self.post(passport, data=dict(action='save')).location
        self.assertNotEqual(v1, v2)
        self.assertNotIn('Замеры трёх кабинетов', self.client.get(v1).text)
        self.assertIn('Замеры трёх кабинетов', self.client.get(v2).text)
        self.assertEqual(self.client.get(v2.replace('/project/2/', '/project/1/')).status_code, 404)
        self.sign_in(2)
        self.assertEqual(self.client.get(manage).status_code, 403)
        self.assertEqual(self.client.get(v2).status_code, 403)
        self.assertEqual(self.client.get(f'{project}/attachments/{second_file}').status_code, 404)
        self.sign_in(1)
        self.assertEqual(self.client.get(v2).status_code, 200)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
            conn.commit()
        self.assertEqual(self.client.get(v2).status_code, 403)

    def test_project_criteria_ai_citation_requires_current_file_and_matching_title(self):
        title = 'В отчёте указаны замеры'
        self.sign_in(1)
        self.assertEqual(self.post('/project/2/criteria', data=dict(action='add', number='1', title=title)).status_code, 302)
        self.assertEqual(self.history_stage(project=2, result='Замеры сделаны',
                                            attachments=[(BytesIO(b'Measurement: 21'), 'measurements.txt')]).status_code, 302)
        self.sign_in(1)
        path = '/teacher/review?work=stage-2-1'
        page = self.client.get(path).text
        file_id = int(re.search(r'/project/2/attachments/(\d+)">measurements.txt', page)[1])
        version = self.history_rows(2)[-1]['id']
        report = dict(summary='Замеры записаны', recommendation='Сверить результат', suggestions=[], files=[],
                      checks=[dict(criterion=title, status='found', quote='Measurement: 21',
                                   attachment=file_id, label='measurements.txt')])
        with patch.dict('os.environ', M_FLOW_OLLAMA_MODEL='mflow-qwen3:4b'), \
                patch('submission_analysis.analyze', return_value=report) as model:
            self.assertEqual(self.post(path, data=dict(action='analyze', analysis_version=str(version))).status_code, 302)
        model.assert_called_once()
        self.assertEqual(model.call_args.kwargs['stage_criteria'], (title,))
        fields = self.client.get(path).text.split('class="rc-decision"', 1)[1]
        self.assertIn('Цитата из ИИ-разбора', fields)
        self.assertIn('Measurement: 21', fields)
        self.assertNotIn('value="confirmed" selected', fields)

    def test_project_criteria_confirmation_belongs_to_one_submission(self):
        self.sign_in(1)
        for title in ('Тема сформулирована', 'Есть описание метода'):
            self.assertEqual(self.post('/project/2/criteria', data=dict(action='add', number='1', title=title)).status_code, 302)
        self.assertEqual(self.history_stage(project=2, result='Первое описание').status_code, 302)
        self.sign_in(1)
        center = '/teacher/review?work=stage-2-1'
        first, second = re.findall(r'name="criterion_status_(\d+)"', self.client.get(center).text)
        form = {'action': 'return', 'number': '1', 'revision': '1', f'criterion_status_{first}': 'confirmed',
                f'criterion_status_{second}': 'revise', f'criterion_note_{second}': 'Опишите метод.'}
        self.assertEqual(self.post(center, data=form).status_code, 302)
        self.sign_in(3)
        self.assertIn('Подтверждено 1 из 2', self.client.get('/project/2').text)
        self.assertNotIn('Первое описание', self.client.get('/project/2/passport').text)
        self.assertEqual(self.history_stage(project=2, revision=2, result='Новое описание').status_code, 302)
        self.assertIn('Подтверждено 0 из 2', self.client.get('/project/2').text)
        self.sign_in(1)
        self.assertIn('Предыдущая сдача · Подтверждено', self.client.get(center).text)
