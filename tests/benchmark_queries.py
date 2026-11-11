"""Локальный замер на отдельной временной базе: python tests/benchmark_queries.py."""
import json
import sqlite3
import statistics
import time
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_teacher_cabinet import TeacherCabinetTests


def measure(case, path):
    queries, connections, timings = [], [], []
    connect = sqlite3.connect
    for _ in range(5):
        statements = []
        opened = []
        def traced_connect(*args, **kwargs):
            opened.append(1)
            conn = connect(*args, **kwargs)
            conn.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith(('SELECT', 'WITH')) else None)
            return conn
        with patch.object(sqlite3, 'connect', traced_connect):
            start = time.perf_counter()
            response = case.client.get(path)
            timings.append((time.perf_counter() - start) * 1000)
        assert response.status_code == 200, path
        queries.append(len(statements)); connections.append(len(opened))
    return dict(selects=max(queries), connections=max(connections), median_ms=round(statistics.median(timings), 2))


if __name__ == '__main__':
    TeacherCabinetTests.setUpClass()
    try:
        case = TeacherCabinetTests()
        case.setUp()
        conn = case.module.get_db()
        for pid in range(10, 50):
            conn.execute('INSERT INTO projects(id,name,owner_id) VALUES(?,?,1)', (pid, f'Проект {pid}'))
            conn.executemany('INSERT INTO project_members VALUES(?,?)', [(pid, 1), (pid, 3)])
            conn.executemany('INSERT INTO tasks(project_id,title,status,assignee_id) VALUES(?,?,?,3)',
                             [(pid, f'Задача {n}', ('todo', 'progress', 'done')[n % 3]) for n in range(20)])
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(5,'empty-teacher',?,'teacher')", (case.password,))
        conn.commit(); conn.close()
        case.sign_in(1)
        results = {path: measure(case, path) for path in ('/teacher', '/project/10', '/project/10/stages')}
        case.sign_in(3)
        results['/ (student)'] = measure(case, '/')
        for path in ('/next', '/next/project/10'):
            results[path] = measure(case, path)
        case.sign_in(5)
        results['/teacher (empty)'] = measure(case, '/teacher')
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        TeacherCabinetTests.tearDownClass()
