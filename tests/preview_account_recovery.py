"""Local review on disposable synthetic accounts. No real SMTP or live DB."""
from html import escape
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_teacher_cabinet import TeacherCabinetTests
from account_recovery_cases import Mailbox
from account_mail import MailSettings


def main():
    TeacherCabinetTests.setUpClass()
    try:
        fixture=TeacherCabinetTests()
        fixture.setUp()
        module=fixture.module
        box=Mailbox()
        module.app.config.update(MAIL_SETTINGS=MailSettings('smtp.invalid',587,'fake','fake','sender@example.org',
            'http://127.0.0.1:5096','starttls'),MAIL_DISPATCHER=box,SESSION_COOKIE_SECURE=False)
        @module.app.after_request
        def deliver_captured(response):
            box.flush()  # Test sender only; never connect to any mail provider.
            return response
        @module.app.route('/__qa_mailbox')
        def captured_mailbox():
            body='<h1>Только тестовые письма</h1><p>Временная база, вымышленные аккаунты, настоящие письма не отправляются.</p><a href="/login">Войти</a>'
            for recipient,subject,message in reversed(box.letters):
                match=re.search(r'http://127\.0\.0\.1:5096/[^\s]+',message)
                link=f'<p><a href="{escape(match[0],quote=True)}">Открыть тестовую ссылку</a></p>' if match else ''
                body+=f'<section class="card"><h2>{escape(subject)}</h2><p>{escape(recipient)}</p><pre style="white-space:pre-wrap">{escape(message)}</pre>{link}</section>'
            return module.render_page('<div class="container"><main>'+body+'</main></div>',title='Тестовый почтовый ящик')
        module.app.run(host='127.0.0.1',port=5096,debug=False,use_reloader=False)
    finally:
        TeacherCabinetTests.tearDownClass()


if __name__=='__main__':
    main()
