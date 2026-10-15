"""Manual browser QA on a throwaway SQLite database, never production."""
import importlib.util
import argparse
import json
from datetime import timedelta
import os
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadlines', action='store_true', help='Add isolated deadline cases for visual QA')
    parser.add_argument('--queue', action='store_true', help='Add isolated teacher queue cases for visual QA')
    parser.add_argument('--supervision', action='store_true', help='Isolated onboarding and replacement without an existing teacher')
    parser.add_argument('--results', action='store_true', help='Isolated student feedback, private grades and a review cycle')
    parser.add_argument('--graphics', action='store_true', help='Add an empty student account for graphics QA')
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix='mflow-notification-preview-') as folder:
        env = dict(M_FLOW_DATABASE_BACKEND='sqlite', M_FLOW_DATABASE=str(Path(folder) / 'preview.db'),
                   M_FLOW_DEBUG='0', M_FLOW_SECRET_KEY='isolated-local-preview-only',
                   M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD='')
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location('notification_preview_app', root / 'app.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.app.config['SESSION_COOKIE_SECURE'] = False  # Loopback HTTP only.
            conn = module.get_db()
            password = generate_password_hash('preview-only-123')
            conn.executemany('INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)',
                [(1, 'preview-teacher', password, 'teacher'), (2, 'preview-student', password, 'student')])
            if options.graphics:
                conn.execute("INSERT INTO users(id,username,password,role) VALUES(100,'preview-empty',?,'student')", (password,))
            conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(1,'Школьная метеостанция',2)")
            conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(1,?)', [(2,)] if options.supervision else [(1,), (2,)])
            if options.supervision:
                conn.execute("INSERT INTO users(id,username,password,role) VALUES(3,'preview-next-teacher',?,'teacher')", (password,))
                conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(2,'Другой проект ученика',2)")
                conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,2)')
            if options.results:
                conn.execute("INSERT INTO users(id,username,password,role) VALUES(3,'preview-engineer',?,'student')", (password,))
                conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(2,'Робот для школьной теплицы',2)")
                conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(?,?)', [(1,3),(2,1),(2,2)])
                conn.executemany('''INSERT INTO project_stages(project_id,number,state,result,teacher_comment,submitted_by,reviewed_by,revision,updated_at)
                    VALUES(?,?,?,?,?,2,1,?,?)''', [
                    (1,1,'done','Согласована тема метеостанции','Тема согласована. Переходите к измерениям.',2,'2026-10-13 10:00:00'),
                    (1,2,'review','Проведены первые измерения','Добавьте условия измерений.',3,'2026-10-15 07:00:00'),
                    (2,1,'progress','Автоматизация полива растений','Уточните цель: какие показатели будет измерять робот и как вы проверите результат.',2,'2026-10-15 08:30:00')])
                conn.execute("INSERT INTO project_defenses(project_id,state,author_id,reviewer_id,teacher_comment,updated_at) VALUES(1,'accepted',2,1,'Запись понятная, ответы аргументированы.','2026-10-14 12:00:00')")
                from grading import CRITERIA
                scores = json.dumps({key:maximum for key,_,maximum in CRITERIA})
                conn.executemany('''INSERT INTO project_assessments
                    (project_id,student_id,teacher_id,student_name,rubric_version,scores_json,note,state)
                    VALUES(1,?,1,?,'school-project-45-v1',?,?,'published')''', [
                    (2,'Тестовый ученик',scores,'Хорошо обоснованы методы исследования.'),
                    (3,'Другой участник',scores,'Приватное замечание другого участника — не показывать ученику 2')])
            from notifications import notify
            if not options.supervision:
                notify(conn, 1, 'preview:submit', 'Этап 1 отправлен на проверку', '', '/project/1/stages', [1])
                notify(conn, 1, 'preview:review', 'Этап 1: нужна доработка',
                       'Уточните, какие измерения будет выполнять метеостанция.', '/project/1/stages', [2])
            if options.deadlines or options.queue:
                today = module.business_today()
                conn.executemany('INSERT INTO tasks(project_id,title,deadline,status,assignee_id) VALUES(1,?,?,?,2)',
                    [(title, (today + timedelta(days=offset)).isoformat() if offset is not None else None, status)
                     for title, offset, status in [('Подключить датчик температуры', -2, 'progress'),
                        ('Проверить точность измерений', 0, 'todo'), ('Собрать корпус станции', 1, 'todo'),
                        ('Подготовить графики результатов', 5, 'todo'), ('Выбрать оборудование', -5, 'done'),
                        ('Обсудить идеи с командой', None, 'todo')]])
                conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision) VALUES(1,1,'review','Согласовать тему метеостанции',2,1)")
            if options.queue:
                conn.execute("INSERT INTO users(id,username,password,role) VALUES(3,'preview-engineer',?,'student')", (password,))
                conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(2,'Школьный робот',3)")
                conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(?,?)', [(1,3),(2,1),(2,3)])
                conn.execute("UPDATE project_stages SET updated_at='2026-10-10 10:00:00' WHERE project_id=1")
                conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision,updated_at) VALUES(2,1,'review','Согласовать тему робота',3,1,'2026-10-01 10:00:00')")
                conn.execute("INSERT INTO project_defenses(project_id,state,author_id,revision,updated_at) VALUES(1,'submitted',2,1,'2026-10-12 10:00:00')")
                conn.executemany('INSERT INTO tasks(id,project_id,title,assignee_id) VALUES(?,1,?,3)',
                                 [(10+n, f'Измерение {n+1}') for n in range(45)])
                conn.executemany('INSERT INTO task_updates(task_id,user_id,body,created_at) VALUES(?,3,?,?)',
                    [(10+n, f'Результат измерения {n+1}: проверен датчик и сохранены данные.', '2026-10-01 12:00:00') for n in range(45)])
            conn.commit()
            conn.close()
            module.app.run(host='127.0.0.1', port=5091, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
