"""Loopback visual review on disposable synthetic data only."""
from pathlib import Path
import secrets
import sys
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'tests')]
from test_teacher_cabinet import TeacherCabinetTests
from account_recovery_cases import Mailbox
from account_mail import MailSettings
from flask import session, redirect, abort, send_from_directory

def main():
    TeacherCabinetTests.setUpClass()
    try:
        fixture=TeacherCabinetTests();fixture.setUp();module=fixture.module;box=Mailbox()
        module.app.config.update(MAIL_SETTINGS=MailSettings('smtp.invalid',587,'fake','fake','sender@example.org','http://127.0.0.1:5112','starttls'),MAIL_DISPATCHER=box,SESSION_COOKIE_SECURE=False)
        @module.app.after_request
        def captured(response):
            box.flush()
            return response
        @module.app.route('/__review/<role>/<screen>')
        def review(role,screen):
            if role not in ('student','teacher','new') or screen not in ('profile','email'):abort(404)
            user_id={'student':3,'teacher':1,'new':4}[role]
            session.clear();session['user_id']=user_id
            session['auth_token']=module.new_session(module.get_db,module.SECRET_KEY,user_id)
            session['csrf_token']=secrets.token_urlsafe(32)
            return redirect('/profile/email' if screen=='email' else '/profile')
        @module.app.route('/design/interface-components/gallery.css')
        def gallery_css():
            return send_from_directory(root/'design/interface-components', 'gallery.css')
        @module.app.route('/__components')
        def components():
            return module.render_page((root/'design/interface-components/gallery.html').read_text(encoding='utf-8'), title='Элементы M-Flow — тестовый стенд')
        fixture.add_candidate(discoverable=1)
        conn = module.get_db()
        conn.execute('UPDATE projects SET name=? WHERE id=1', ('Школьная медиастудия',))
        conn.execute('UPDATE projects SET name=? WHERE id=2', ('Мост будущего',))
        for project_id, name in ((4,'Умная теплица'),(5,'Чистый школьный двор'),(6,'История нашего района'),(7,'Макет школьной библиотеки')):
            conn.execute('INSERT INTO projects(id,name,owner_id) VALUES(?,?,3)', (project_id,name))
            conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(?,?)',[(project_id,1),(project_id,3)])
        conn.commit(); conn.close()
        def stage(project_id, user, number=1, revision=0, action='submit', **extra):
            fixture.sign_in(user)
            data=dict(number=str(number),revision=str(revision),action=action,result='Исследовали тему, подготовили план и первые материалы.')
            data.update(extra)
            assert fixture.post(f'/project/{project_id}/stages',data=data).status_code == 302
        stage(4,3)
        stage(5,3)
        stage(5,1,revision=1,action='return',teacher_comment='Уточните цель исследования и добавьте сравнение двух вариантов решения. Начало хорошее — теперь нужны аргументы.')
        for n in range(1,6):
            stage(6,3,number=n,**({'presentation_url':'https://example.org/presentation'} if n==5 else {}))
            stage(6,1,number=n,revision=1,action='accept',teacher_comment='Этап принят. Хорошая работа с материалами.')
        stage(7,3,action='save',result='Подготовили первый набросок пространства библиотеки.')
        group_id=fixture.group_create(name='10А · Проектная мастерская')
        assert fixture.group_join(fixture.group_code(group_id)).status_code==302
        fixture.sign_in(3)
        assert fixture.post('/teams/invite',data=dict(project_id='2',student_id='4',role_text='Исследователь',message='Приглашение для проверки навигации.')).status_code == 302
        module.app.run(host='127.0.0.1',port=5112,debug=False,use_reloader=False)
    finally:
        TeacherCabinetTests.tearDownClass()


if __name__ == "__main__":
    main()
