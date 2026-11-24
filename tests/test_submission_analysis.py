from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from attachment_text import extract_excerpt
import submission_analysis


def document(text):
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', text)
    return buffer.getvalue()


class SubmissionAnalysisTests(TestCase):
    def test_model_receives_excerpts_and_links_only_verified_quotes(self):
        raw = document('''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:body><w:p><w:r><w:t>Датчик А: 20,1 °C</w:t></w:r></w:p></w:body></w:document>''')
        reply = dict(summary='В документе есть замер', suggestions=['Сверить значения'],
                     checks=[dict(criterion='Измерение', status='found', source='file-16', quote='20,1 °C'),
                             dict(criterion='Другой датчик', status='found', source='description', quote='20,1 °C'),
                             dict(criterion='Заявленный вывод', status='found', source='http://note', quote='опыт')])
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'message': {'content': json.dumps(reply)}}).encode()
        with patch.dict('os.environ', {'M_FLOW_OLLAMA_PORT': '11435'}), patch.object(submission_analysis, 'HTTPConnection') as transport:
            transport.return_value.getresponse.return_value = response
            report = submission_analysis.analyze(
                'qwen3:8b', {'result': 'Провели опыт', 'teacher_comment': ''},
                [(16, 'report.docx', raw), (17, 'scan.png', b'raw')], 'Этап', 'Проверить замеры', None)
        transport.assert_called_once_with('127.0.0.1', 11435, timeout=80)
        method, route, request_body, _ = transport.return_value.request.call_args.args
        self.assertEqual((method, route), ('POST', '/api/chat'))
        model_data = json.loads(json.loads(request_body)['messages'][1]['content'])
        self.assertEqual([row['id'] for row in model_data['sources']], ['file-16', 'description'])
        self.assertEqual([item['status'] for item in report['checks']], ['found', 'missing', 'missing'])
        self.assertEqual(report['checks'][0]['attachment'], 16)
        self.assertIn('для скачивания', report['files'][1]['coverage'])
        self.assertIn('откройте оригиналы', report['recommendation'])

    def test_unreadable_office_content_and_large_text_are_reported(self):
        samples = [
            ('report.docx', document('<!DOCTYPE w [<!ENTITY x SYSTEM "file:///private">]><w>&x;</w>')),
            ('report.txt', b'a\0b'),
            ('broken.pptx', b'invalid'),
        ]
        for name, raw in samples:
            with self.subTest(name=name):
                self.assertEqual(extract_excerpt(name, raw)[0], '')
        excerpt, status = extract_excerpt('large.csv', b'x' * 1600)
        self.assertEqual(len(excerpt), 1200)
        self.assertIn('фрагмент', status)
