"""Create the two synthetic classroom accounts in the disposable online demo."""

from contextlib import closing
from datetime import timedelta
import os
from pathlib import Path

from werkzeug.security import generate_password_hash

from database import insert_id
from deadlines import business_today
from local_assistant import DEMO_TEACHER, DEMO_STUDENT
from project_rules import create_owned_project
from project_stages import change_stage, get_stages

PROJECT = 'Метеостанция'
FIRST = 'Сравнил датчики A и B. Сделал по три замера.'
SECOND = 'Датчик A: 20,1; 20,2; 20,0 °C. Датчик B: 20,4; 20,3; 20,4 °C. Добавил экспорт в CSV.'
INITIAL = 'READINGS = [("A", 20.1), ("B", 20.4)]\nprint(READINGS)\n'
REVISED = '''import csv

READINGS = [("A", 20.1), ("B", 20.4)]

def export_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["Датчик", "Температура, °C"])
        writer.writerows(READINGS)

export_csv("readings.csv")
'''


def seed():
    password = os.environ.get('M_FLOW_CLOUD_DEMO_PASSWORD', '')
    database = Path(os.environ.get('M_FLOW_DATABASE', ''))
    if (os.environ.get('M_FLOW_CLOUD_DEMO') != '1' or os.environ.get('M_FLOW_DATABASE_BACKEND') != 'sqlite'
            or not database.is_absolute() or database.name != 'mflow-cloud-demo.db'):
        raise SystemExit('Для заполнения примеров нужна отдельная демобаза mflow-cloud-demo.db.')
    if len(password) < 12:
        raise SystemExit('Задайте отдельный пароль демо длиной от 12 символов в M_FLOW_CLOUD_DEMO_PASSWORD.')

    from app import get_db

    with closing(get_db()) as conn:
        users = {row['username']: dict(row) for row in conn.execute('SELECT id,username,role FROM users')}
        if not users:
            conn.executemany('INSERT INTO users(username,password,role) VALUES(?,?,?)',
                             ((DEMO_TEACHER, generate_password_hash(password), 'teacher'),
                              (DEMO_STUDENT, generate_password_hash(password), 'student')))
            users = {row['username']: dict(row) for row in conn.execute('SELECT id,username,role FROM users')}
            teacher_id, student_id = users[DEMO_TEACHER]['id'], users[DEMO_STUDENT]['id']
            gid = insert_id(conn, 'INSERT INTO learning_groups(teacher_id,name,description) VALUES(?,?,?)',
                            (teacher_id, '10 А · демо', 'Учебный пример для показа M-Flow.'))
            conn.execute('INSERT INTO learning_group_members(group_id,student_id) VALUES(?,?)', (gid, student_id))
            aid = insert_id(conn, '''INSERT INTO group_assignments(group_id,title,goal,due_on,materials,criteria)
                VALUES(?,?,?,?,?,?)''', (gid, PROJECT, 'Сравнить показания двух датчиков и сохранить замеры.',
                                        (business_today() + timedelta(days=7)).isoformat(), 'Три замера на каждом датчике.',
                                        'Показаны результаты двух датчиков. Указаны единицы. Данные выгружаются в CSV.'))
            pid = create_owned_project(conn, student_id, PROJECT)
            conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(?,?)', (pid, teacher_id))
            conn.execute('INSERT INTO group_assignment_projects(assignment_id,student_id,project_id) VALUES(?,?,?)',
                         (aid, student_id, pid))
            conn.commit()
        else:
            if set(users) != {DEMO_TEACHER, DEMO_STUDENT}:
                raise SystemExit('В демобазе обнаружены посторонние пользователи.')
            project = conn.execute('SELECT id FROM projects WHERE owner_id=? AND name=?',
                                   (users[DEMO_STUDENT]['id'], PROJECT)).fetchone()
            if not project:
                raise SystemExit('Существующая демобаза не содержит учебный проект.')
            pid = project['id']

        revision = get_stages(conn, pid)[0]['revision']
        if revision == 0:
            change_stage(conn, pid, users[DEMO_STUDENT], {'number': '1', 'revision': '0', 'action': 'submit', 'result': FIRST},
                         source_snapshot=({'weather.py': INITIAL}, 0))
        if revision <= 1:
            change_stage(conn, pid, users[DEMO_TEACHER], {'number': '1', 'revision': '1', 'action': 'return',
                         'teacher_comment': 'Добавьте единицы измерения и покажите экспорт.'})
        if revision <= 2:
            change_stage(conn, pid, users[DEMO_STUDENT], {'number': '1', 'revision': '2', 'action': 'submit', 'result': SECOND},
                         source_snapshot=({'weather.py': REVISED}, 0))


if __name__ == '__main__':
    seed()
