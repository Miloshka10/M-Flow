"""Manual interface QA, using only synthetic data in a temporary SQLite DB."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import timedelta
from unittest.mock import patch
from werkzeug.security import generate_password_hash


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix='mflow-interface-preview-') as folder:
        settings = dict(M_FLOW_DATABASE_BACKEND='sqlite', M_FLOW_DATABASE=str(Path(folder) / 'preview.db'),
                        M_FLOW_DEBUG='0', M_FLOW_SECRET_KEY='isolated-interface-preview-only',
                        M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD='')
        with patch.dict(os.environ, settings):
            spec = importlib.util.spec_from_file_location('interface_preview_app', root / 'app.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.app.config['SESSION_COOKIE_SECURE'] = False  # Loopback HTTP only.
            conn = module.get_db()
            password = generate_password_hash('preview-only-123')
            conn.executemany('INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)', [
                (1,'preview-teacher',password,'teacher'),(2,'preview-student',password,'student'),
                (3,'preview-engineer',password,'student'),(4,'preview-outsider',password,'student'),
                (100,'preview-empty',password,'student')])
            conn.executemany('INSERT INTO projects(id,name,owner_id) VALUES(?,?,?)', [
                (1,'Умная теплица',2),(2,'Карта тихих мест',2),(3,'Школьный экогид',2),
                (4,'Энергия солнца',2),(5,'Чужой закрытый проект',4)])
            conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(?,?)',
                [(1,1),(1,2),(1,3),(2,1),(2,2),(3,1),(3,2),(4,2),(5,4)])
            conn.executemany('''INSERT INTO project_stages(project_id,number,state,result,teacher_comment,submitted_by,reviewed_by,revision)
                VALUES(?,?,?,?,?,2,?,?)''', [
                (1,1,'done','Автоматизация полива и контроль влажности.','Тема согласована.',1,2),
                (1,2,'done','Собран корпус и подключён контроллер.','Можно переходить к испытаниям.',1,2),
                (1,3,'progress','Проверили запуск полива по показаниям датчика.','Добавьте таблицу измерений и сравните показания двух датчиков.',1,2),
                (2,1,'review','Исследуем тихие места для самостоятельной работы в школе.','',None,1)])
            conn.executemany("INSERT INTO project_stages(project_id,number,state,result,submitted_by,reviewed_by,revision) VALUES(3,?,'done',?,2,1,2)",
                             [(n,f'Принятый результат этапа {n}') for n in range(1,6)])
            conn.execute("UPDATE project_stages SET presentation_url='https://example.com/presentation' WHERE project_id=3 AND number=5")
            conn.execute('''INSERT INTO project_defense_plans(project_id,problem,goal,process,outcome,conclusion,updated_by)
                VALUES(3,?,?,?,?,?,2)''', (
                'В школе не было удобной информации о раздельном сборе отходов. Ученики не знали, какие материалы можно сдавать.',
                'Сделать понятный экогид с картой пунктов сбора и проверить его на учениках нашего класса.',
                'Собрали информацию, проверили адреса и подготовили интерактивную карту. Я отвечал за опрос, участники команды — за карту и тексты.',
                'Гид протестировали 18 учеников. После знакомства с ним 15 участников смогли правильно выбрать пункт приёма.',
                'Короткие инструкции помогают перейти от интереса к действию. Дальше хотим добавить другие районы и регулярно проверять адреса.'))
            conn.execute("INSERT INTO project_defenses(project_id,state,video_url,description,author_id,reviewer_id,teacher_comment,revision) VALUES(3,'accepted','https://example.com/video','Запись защиты школьного экогида.',2,1,'Защита принята.',2)")
            today = module.business_today()
            for title, offset, status, assignee, stage, priority in [
                ('Сравнить показания датчиков',0,'progress',2,3,'high'),
                ('Добавить таблицу измерений',1,'todo',2,3,'normal'),
                ('Собрать схему подключения',-1,'progress',3,3,'normal'),
                ('Собрать корпус теплицы',-3,'done',3,2,'normal'),
                ('Подготовить описание испытаний',3,'todo',None,3,'normal')]:
                conn.execute('''INSERT INTO tasks(project_id,title,deadline,status,assignee_id,stage_number,priority,description)
                    VALUES(1,?,?,?,?,?,?,?)''', (title,(today+timedelta(days=offset)).isoformat(),status,assignee,stage,priority,
                    'Провести измерения в одинаковых условиях и сохранить результаты. Все данные в этом проекте тестовые.'))
            conn.execute("INSERT INTO task_updates(task_id,user_id,body) VALUES(1,2,'Первое измерение выполнено. Готовлю сравнение.')")
            conn.execute("INSERT INTO learning_groups(id,teacher_id,name,description) VALUES(1,1,'10 «А» — проектная работа','От первой идеи до своего результата.')")
            conn.executemany('INSERT INTO learning_group_members(group_id,student_id,project_id) VALUES(1,?,?)', [(2,1),(3,None)])
            conn.execute("INSERT INTO student_profiles(user_id,class_name,direction,skills_json,bio,discoverable) VALUES(3,'10 А','Инженерия',?,'Помогу собрать прототип и подключить датчики.',1)", (json.dumps(['Электроника','Arduino']),))
            conn.execute("INSERT INTO team_member_roles(project_id,user_id,role_text) VALUES(1,3,'Разработчик электроники')")
            from grading import CRITERIA
            conn.execute('''INSERT INTO project_assessments(project_id,student_id,teacher_id,student_name,rubric_version,scores_json,note,state)
                VALUES(3,2,1,'preview-student','school-project-45-v1',?,'Выводы подтверждены исследованием. Отличная работа.','published')''',
                (json.dumps({key:maximum for key,_,maximum in CRITERIA}),))
            from notifications import notify
            notify(conn,2,'preview:review','Тема отправлена на проверку','','/project/2/stages',[1])
            notify(conn,1,'preview:return','Этапу нужны дополнения','Добавьте измерения и сравнение датчиков.','/project/1/stages',[2])
            conn.commit()
            conn.close()
            module.app.run(host='127.0.0.1',port=5091,debug=False,use_reloader=False)


if __name__ == '__main__':
    main()
