"""Loopback visual review on disposable synthetic data only."""
from pathlib import Path
import secrets
import sys
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'tests')]
from test_teacher_cabinet import TeacherCabinetTests
from account_recovery_cases import Mailbox
from account_mail import MailSettings
from flask import session, redirect, abort

def main():
    TeacherCabinetTests.setUpClass()
    try:
        fixture=TeacherCabinetTests();fixture.setUp();module=fixture.module;box=Mailbox()
        module.app.config.update(MAIL_SETTINGS=MailSettings('smtp.invalid',587,'fake','fake','sender@example.org','http://127.0.0.1:5116','starttls'),MAIL_DISPATCHER=box,SESSION_COOKIE_SECURE=False)
        @module.app.after_request
        def captured(response):
            box.flush()
            return response
        @module.app.route('/__workspace/<role>/<screen>')
        def review(role,screen):
            roles={'student':3,'teacher':1,'new':4,'empty-teacher':5}
            screens={'home':'/','review':'/teacher#review-queue','project':'/project/2','work':'/project/2/stages','submitted':'/project/4/stages#stage-1-result','returned':'/project/5/stages#stage-1-result','finished':'/project/6','draft':'/project/7/stages','resubmitted':'/project/8/stages#stage-1-result','accepted-stages':'/project/9','long':'/project/10/stages'}
            if role not in roles or screen not in screens:abort(404)
            uid=roles[role]
            session.clear();session['user_id']=uid
            session['auth_token']=module.new_session(module.get_db,module.SECRET_KEY,uid)
            session['csrf_token']=secrets.token_urlsafe(32)
            return redirect(screens[screen])
        fixture.add_candidate(discoverable=1)
        with module.get_db() as conn:
            conn.execute("INSERT INTO users(id,username,password,role) VALUES(5,'new-teacher',?,'teacher')",(fixture.password,))
            conn.commit()
        conn = module.get_db()
        conn.execute('UPDATE projects SET name=? WHERE id=1', ('Школьная медиастудия',))
        conn.execute('UPDATE projects SET name=? WHERE id=2', ('Мост будущего',))
        for project_id, name in ((4,'Умная теплица'),(5,'Чистый школьный двор'),(6,'История нашего района'),(7,'Макет школьной библиотеки'),(8,'Переработка пластиковых отходов в школьной мастерской'),(9,'Архив школьного музея'),(10,'П' * 80)):
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
        stage(8,3)
        stage(8,1,revision=1,action='return',teacher_comment='Добавьте измеримые критерии и сравнение двух подходов.')
        stage(8,3,revision=2,result='Добавили критерии: стоимость, прочность, время изготовления. Сравнили два способа переработки и выбрали вариант для мастерской.')
        from datetime import timedelta
        with module.get_db() as conn:
            conn.execute("UPDATE task_updates SET body='Провели первые измерения и подготовили схему эксперимента.'")
            for pid in (2,4,5,7,8):
                for i,(title,status) in enumerate((('Сформулировать цель и проверить гипотезу','done'),('Сравнить два варианта решения','progress'),('Подготовить материалы для обсуждения','todo'))):
                    conn.execute('INSERT INTO tasks(project_id,title,deadline,status,assignee_id,stage_number,description) VALUES(?,?,?,?,3,1,?)',(pid,title,(module.business_today()+timedelta(days=i-1)).isoformat(),status,'Обсудите варианты с командой. Добавьте вывод в результат этапа.'))
            conn.commit()
        fixture.sign_in(3)
        assert fixture.post('/project/6/defense',data=dict(action='submit',revision=0,video_url='https://example.org/defense',description='Готовая защита проекта.')).status_code==302
        fixture.sign_in(1)
        assert fixture.post('/project/6/defense',data=dict(action='accept',revision=1)).status_code==302
        for n in range(1,6):
            stage(9,3,number=n,**({'presentation_url':'https://example.org/presentation'} if n==5 else {}))
            stage(9,1,number=n,revision=1,action='accept')
        fixture.seed_result_grade(project=6)
        group_id=fixture.group_create(name='10А · Проектная мастерская')
        assert fixture.group_join(fixture.group_code(group_id)).status_code==302
        fixture.sign_in(3)
        assert fixture.post('/teams/invite',data=dict(project_id='2',student_id='4',role_text='Исследователь',message='Приглашение для проверки навигации.')).status_code == 302
        module.app.run(host='127.0.0.1',port=5116,debug=False,use_reloader=False)
    finally:
        TeacherCabinetTests.tearDownClass()


if __name__ == "__main__":
    main()
