"""Loopback-only access review with disposable fixtures and a fake mail dispatcher."""
from pathlib import Path
import secrets
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'tests')]
from test_teacher_cabinet import TeacherCabinetTests
from account_recovery_cases import Mailbox
from account_mail import MailSettings
from flask import session, redirect, abort


def main():
    TeacherCabinetTests.setUpClass()
    try:
        fixture = TeacherCabinetTests()
        fixture.setUp()
        module, box = fixture.module, Mailbox()
        mail = MailSettings('smtp.invalid', 587, 'fake', 'fake', 'sender@example.org',
                            'http://127.0.0.1:5114', 'starttls')
        module.app.config.update(MAIL_SETTINGS=mail, MAIL_DISPATCHER=box, SESSION_COOKIE_SECURE=False)

        @module.app.after_request
        def captured(response):
            box.flush()
            return response

        @module.app.route('/__access/<role>/<state>/<screen>')
        def access_state(role, state, screen):
            states = ('none', 'pending', 'verified', 'changing', 'old-ok', 'new-ok', 'long', 'unavailable')
            if role not in ('student', 'teacher') or state not in states or screen not in ('profile', 'email'):
                abort(404)
            uid = 3 if role == 'student' else 1
            module.app.config['MAIL_SETTINGS'] = None if state == 'unavailable' else mail
            current = f'account-{uid}@example.org' if state in ('verified', 'changing', 'old-ok', 'new-ok', 'long', 'unavailable') else ''
            pending = f'new-address-{uid}@example.org' if state in ('pending', 'changing', 'old-ok', 'new-ok') else ''
            if state == 'long':
                current = 'project.account.' + 'x' * 48 + '@school-projects-for-the-whole-class.example.org'
            with module.get_db() as conn:
                conn.execute('INSERT INTO account_emails(user_id,email,pending_email,old_approved,new_approved) VALUES(?,?,?,?,?) '
                             'ON CONFLICT(user_id) DO UPDATE SET email=excluded.email,pending_email=excluded.pending_email,'
                             'old_approved=excluded.old_approved,new_approved=excluded.new_approved',
                             (uid, current, pending, int(state == 'old-ok'), int(state == 'new-ok')))
                conn.commit()
            session.clear()
            session.update(user_id=uid, auth_token=module.new_session(module.get_db, module.SECRET_KEY, uid),
                           csrf_token=secrets.token_urlsafe(32))
            return redirect('/profile/email' if screen == 'email' else '/profile')

        @module.app.route('/__access-public/<state>')
        def public_state(state):
            module.app.config['MAIL_SETTINGS'] = mail
            if state in ('reset-ready', 'confirm-ready'):
                # Format-ready preview only: this capability is not valid in the database.
                return redirect(('/reset-password' if state == 'reset-ready' else '/email/confirm') + '#token=' + 'a' * 43)
            if state not in ('reset-success', 'confirmed', 'pending-confirmation'):
                abort(404)
            reset = state == 'reset-success'
            with module.app.test_request_context('/reset-password' if reset else '/email/confirm'):
                return module.mail_response('Пароль изменён' if reset else 'Подтверждение почты',
                    'Все прежние сеансы завершены. Войдите с новым паролем через обычную форму.' if reset else '', '', '', ready=False,
                    notice='' if reset else 'Почта подтверждена. Теперь она доступна для восстановления пароля.' if state == 'confirmed' else
                    'Подтверждение получено. Для завершения смены подтвердите второй ящик.',
                    state='form' if reset else state, login_url='/teacher/login' if reset else '/login')

        module.app.run(host='127.0.0.1', port=5114, debug=False, use_reloader=False)
    finally:
        TeacherCabinetTests.tearDownClass()


if __name__ == '__main__':
    main()
