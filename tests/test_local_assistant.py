from contextlib import closing
import io
import json
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import local_assistant as assistant


def archive(files):
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    buffer.seek(0)
    return buffer


def compare(before, after):
    return assistant.compare_sources(assistant.read_source_archive(archive(before)),
                                     assistant.read_source_archive(archive(after)))


class LocalAssistantTests(unittest.TestCase):
    def test_identical_small_and_functional_changes_are_distinguished(self):
        original = {'app.py': 'print("temperature")\r\n', 'diagram.png': 'omitted'}
        same = compare(original, {'app.py': 'print("temperature")\n'})
        self.assertEqual(same['files'], [])
        self.assertEqual(same['evidence'], [])
        self.assertEqual(same['ignored'], 1)
        small = compare(original, {'app.py': 'print("temperature °C")\n'})
        self.assertIn('Небольшой', small['status'])
        self.assertEqual((small['added'], small['removed']), (1, 1))
        revised = {'app.py': original['app.py'], 'export.py': 'def export_csv(rows):\n    return csv.writer(rows)\n'}
        result = compare(original, revised)
        self.assertEqual(result['files'][0]['kind'], 'added')
        self.assertEqual(result['declarations'], [dict(path='export.py', kind='added', name='export_csv')])
        self.assertIn('def export_csv', result['evidence'][0]['excerpt'])
        removed = compare(revised, original)
        self.assertEqual(removed['evidence'][0]['kind'], 'removed')
        self.assertEqual(removed['removed'], 2)

    def test_invalid_or_ambiguous_archives_are_rejected(self):
        for data in ({'../file.py': 'a'}, {'src/a.py': 'a', 'SRC/A.PY': 'b'},
                     {'large.py': 'x' * (assistant.FILE_LIMIT + 1)}, {'app.py': b'\xff'}, {'app.py': b'a\0b'}):
            with self.subTest(entries=tuple(data)), self.assertRaises(ValueError):
                assistant.read_source_archive(archive(data))

    def test_text_verdict_distinguishes_removed_words_links_and_first_submission(self):
        old = dict(result='Сделан график температуры и влажности', resource_url='https://example.com/a')
        new = dict(old, result='Сделан график температуры')
        label, evidence = assistant.text_difference(new, old)
        self.assertIn('Небольшая', label)
        self.assertEqual(evidence[0]['kind'], 'removed')
        self.assertEqual(evidence[0]['excerpt'], 'и влажности')
        label, _ = assistant.text_difference(dict(old, resource_url='https://example.com/b'), old)
        self.assertIn('Ссылка на материал изменилась.', label)
        self.assertIn('Первая', assistant.text_difference(old, None)[0])

    def test_model_request_is_loopback_and_unsubstantiated_output_is_downgraded(self):
        raw = {'summary': 'Сводка проверки', 'suggestions': ['Приложите результаты'],
               'checks': [dict(criterion='Повторные замеры', status='found', evidence='несуществующая цитата'),
                          dict(criterion='Влажность', status='found', evidence='humidity_sensor()')],
               'changes': [dict(description='Добавлен CSV', source='missing.py', quote='csv'),
                           dict(description='Датчик снят', source='main.py', quote='humidity_sensor()')]}
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'message': {'content': json.dumps(raw)}}).encode()
        evidence = [dict(source='main.py', kind='removed', excerpt='humidity_sensor()')]
        with patch.dict('os.environ', {'M_FLOW_OLLAMA_MODEL': 'qwen3:4b-instruct', 'M_FLOW_OLLAMA_PORT': '11435'}), \
                patch.object(assistant, 'HTTPConnection') as transport:
            transport.return_value.getresponse.return_value = response
            result = assistant.advise('Температура в °C', 'Этап 2', {'criteria': 'Три замера'}, evidence=evidence)
            transport.assert_called_once_with('127.0.0.1', 11435, timeout=80)
            method, route, body, _ = transport.return_value.request.call_args.args
        self.assertEqual((method, route), ('POST', '/api/chat'))
        payload = json.loads(body)
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['format'], assistant.SCHEMA)
        self.assertEqual([item['status'] for item in result['checks']], ['Не подтверждено'] * 2)
        self.assertEqual([item['source'] for item in result['changes']], ['main.py'])

    def test_unavailable_model_does_not_leave_the_process_slot_busy(self):
        with patch.dict('os.environ', {'M_FLOW_OLLAMA_MODEL': 'qwen3:4b-instruct'}), \
                patch.object(assistant, 'HTTPConnection') as transport:
            transport.return_value.request.side_effect = ConnectionRefusedError
            for _ in range(2):
                with self.assertRaisesRegex(assistant.AssistantUnavailable, 'недоступна'):
                    assistant.advise('Данные замеров', 'Первый этап')


class AssistantRouteCases:
    def test_local_assistant_draft_checks_access_and_does_not_change_stage(self):
        self.sign_in(3)
        path = '/project/1/stages/1/advice'
        data = {'result': 'Замер 20 °C', 'revision': '0'}
        example = dict(summary='Совет', checks=[], changes=[], suggestions=[])
        with patch.dict('os.environ', {'M_FLOW_OLLAMA_MODEL': 'qwen3:4b-instruct'}), \
                patch.object(assistant, 'advise', return_value=example) as advise:
            self.assertIn('Получить совет по доработке', self.client.get('/project/1/stages').text)
            snapshot = self.database_snapshot()
            self.assertEqual(self.client.post(path, json=data).status_code, 400)
            headers = {'X-CSRF-Token': 'test-form-token'}
            self.assertEqual(self.client.post('/project/3/stages/1/advice', json=data, headers=headers).status_code, 403)
            self.assertEqual(self.client.post(path, json=dict(data, revision='1'), headers=headers).status_code, 409)
            response = self.client.post(path, json=data, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, example)
            self.assertEqual(advise.call_count, 1)
            self.assertEqual(self.database_snapshot(), snapshot)

    def test_local_assistant_review_reports_files_even_when_model_is_down(self):
        review = '/teacher/review/analysis/1/1'
        with patch.dict('os.environ', {'M_FLOW_OLLAMA_MODEL': 'qwen3:4b-instruct'}), \
                patch.object(assistant, 'advise', side_effect=assistant.AssistantUnavailable('Ollama недоступна')) as advise:
            self.sign_in(3)
            submission = dict(action='submit', number='1', revision='0', result='Замер температуры')
            before = self.database_snapshot()
            bad_archive = (archive({'../weather.py': 'old'}), 'bad.zip')
            self.assertEqual(self.post('/project/1/stages', data=dict(submission, source_archive=bad_archive)).status_code, 400)
            self.assertEqual(self.database_snapshot(), before)
            first = (archive({'src/weather.py': 'print(20)\n'}), 'first.zip')
            self.assertEqual(self.post('/project/1/stages', data=dict(submission, source_archive=first)).status_code, 302)
            self.sign_in(1)
            page = self.client.get(review).text
            self.assertIn('Это первая сдача с приложенными исходниками.', page)
            self.assertNotIn('data-archive-verdict', page)
            self.assertEqual(self.stage_post(1, revision=1, action='return', teacher_comment='Нужна единица').status_code, 302)
            self.sign_in(3)
            second = [(io.BytesIO(b'print(20)\n'), 'src/weather.py'),
                      (io.BytesIO(b'def export_csv():\n    pass\n'), 'src/export.py')]
            revised = dict(submission, revision='2', result='Замер температуры 20 °C', source_files=second)
            self.assertEqual(self.post('/project/1/stages', data=revised).status_code, 302)
            self.assertEqual(self.client.get(review).status_code, 403)
            self.sign_in(1)
            self.assertEqual(self.client.get('/teacher/review/analysis/3/1').status_code, 403)
            page = self.client.get(review)
            self.assertEqual(page.status_code, 200)
            self.assertIn('Отправки №1 и №2', page.text)
            self.assertIn('Небольшая правка текста', page.text)
            self.assertIn('Объявления функций Python', page.text)
            self.assertIn('src/export.py', page.text)
            self.assertNotIn('type="file"', page.text)
            page = self.client.get(review, query_string={'back': '/teacher/review?group=7&work=stage-1-1#review-detail'})
            self.assertIn('href="/teacher/review?group=7&amp;work=stage-1-1#review-detail"', page.text)
            page = self.client.get(review, query_string={'back': 'https://example.com/elsewhere'})
            self.assertNotIn('example.com/elsewhere', page.text)
            self.assertEqual(self.post(review, data={'revision': '1'}).status_code, 409)
            snapshot = self.database_snapshot()
            response = self.post(review, data={'revision': '3'})
            self.assertEqual(response.status_code, 503)
            self.assertIn('Заметные изменения'.lower(), response.text.lower())
            self.assertIn('export.py', response.text)
            self.assertIn('Ollama недоступна', response.text)
            self.assertEqual(advise.call_count, 1)
            self.assertEqual(self.database_snapshot(), snapshot)

    def test_local_assistant_keeps_saved_drafts_and_existing_submissions_without_sources(self):
        self.sign_in(3)
        self.assertEqual(self.stage_post(3).status_code, 302)
        self.assertEqual(self.stage_post(1, revision=1, action='return', teacher_comment='Нужно уточнение').status_code, 302)
        self.sign_in(3)
        with patch.dict('os.environ', {'M_FLOW_OLLAMA_MODEL': 'qwen3:4b-instruct'}):
            source = lambda: (archive({'weather.py': 'print(20)'}), 'weather.zip')
            saved = dict(action='save', number='1', revision='2', result='Черновик', source_archive=source())
            self.assertEqual(self.post('/project/1/stages', data=saved).status_code, 302)
            with closing(self.module.get_db()) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_sources').fetchone()[0], 0)
            self.assertEqual(self.post('/project/1/stages', data=dict(saved, action='submit', revision='3',
                                                                    source_archive=source())).status_code, 302)
            self.sign_in(1)
            page = self.client.get('/teacher/review/analysis/1/1').text
            self.assertIn('К предыдущей сдаче исходники не приложены.', page)
            self.assertNotIn('data-archive-verdict', page)
