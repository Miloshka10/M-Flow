"""Local-only PostgreSQL dump/restore drill on synthetic databases.

Requires PostgreSQL 18 tools and empty loopback databases mflow_drill_source
and mflow_restore_check. Supply their direct URLs in M_FLOW_DRILL_SOURCE_URL
and M_FLOW_DRILL_TARGET_URL. The caller owns and cleans up both databases.
"""
from contextlib import closing
from datetime import date, timedelta
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postgres_app_check import fingerprint, phase
from postgres_backup import create_backup
from postgres_restore_check import require_empty, restore_verified_archive


PREFIX = 'classroom_drill'


def validate(url, database):
    parsed = urlsplit(url)
    if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.path != '/' + database:
        raise ValueError('Use the dedicated loopback drill databases.')
    return url


def seed(url):
    validate(url, 'mflow_drill_source')
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
        count = conn.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
        if count:
            raise ValueError('The synthetic source database must be empty.')
    import app
    import postgres_app_check
    from unittest.mock import patch
    from learning_groups import create_group
    from group_assignments import save_assignment, link_project

    # Reuse the real application exercise on this dedicated source name.
    with patch.object(postgres_app_check, 'test_environment', lambda candidate: validate(candidate, 'mflow_drill_source')):
        phase('create')
    with closing(app.get_db()) as conn:
        teacher = dict(conn.execute('SELECT id,role FROM users WHERE username=?', (PREFIX + '_teacher',)).fetchone())
        student = dict(conn.execute('SELECT id,role FROM users WHERE username=?', (PREFIX + '_student',)).fetchone())
        project = conn.execute('SELECT id FROM projects WHERE owner_id=?', (student['id'],)).fetchone()[0]
        gid = create_group(conn, teacher, {'name': 'Класс восстановления'})
        conn.execute('INSERT INTO learning_group_members(group_id,student_id) VALUES(?,?)', (gid, student['id']))
        conn.commit()
        aid = save_assignment(conn, teacher, gid, dict(title='Учебная проверка', goal='Проверить восстановление',
                                                       due_on=(date.today() + timedelta(days=14)).isoformat()))
        link_project(conn, student, gid, aid, {'action': 'link_project', 'project_id': str(project)})


def verify(url):
    validate(url, 'mflow_restore_check')
    expected = os.environ['MF_CHECK_FINGERPRINT']
    if fingerprint(url) != expected:
        raise AssertionError('Restored rows differ from the synthetic source.')
    import app
    if fingerprint(url) != expected:
        raise AssertionError('Startup modified restored rows.')
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with closing(app.get_db()) as conn:
        work = conn.execute("""SELECT p.id,a.id AS assignment_id,a.group_id FROM projects p
            JOIN group_assignment_projects ap ON ap.project_id=p.id
            JOIN group_assignments a ON a.id=ap.assignment_id WHERE p.name=?""", (PREFIX,)).fetchone()
    base = f'/project/{work["id"]}'
    assignment = f'/groups/{work["group_id"]}/assignments/{work["assignment_id"]}'
    for role, login_path in (('student', '/login'), ('teacher', '/teacher/login'), ('other', '/login')):
        client = app.app.test_client()
        assert client.get(login_path).status_code == 200
        with client.session_transaction() as session:
            token = session['csrf_token']
        logged_in = client.post(login_path, data=dict(username=PREFIX + '_' + role,
            password=os.environ['MF_CHECK_PASSWORD'], csrf_token=token))
        assert logged_in.status_code == 302
        if role == 'other':
            assert all(client.get(path).status_code == 403 for path in (base, assignment, base + '/assessment'))
            continue
        assert 'Учебная проверка' in client.get(assignment).text
        assert 'Тестовый результат' in client.get(base + '/history').text
        assert 'Защита принята' in client.get(base + '/defense').text
        if role == 'student':
            assert '45 / 45' in client.get(base + '/assessment').text
        else:
            assert 'Этапы и защита приняты: 1' in client.get(assignment).text


def drill():
    source = validate(os.environ['M_FLOW_DRILL_SOURCE_URL'], 'mflow_drill_source')
    target = validate(os.environ['M_FLOW_DRILL_TARGET_URL'], 'mflow_restore_check')
    with psycopg.connect(target, autocommit=True, connect_timeout=5) as conn:
        require_empty(conn)
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PG', 'M_FLOW_', 'DATABASE_URL', 'MF_CHECK_'))}
    env.update(M_FLOW_DATABASE_BACKEND='postgres', M_FLOW_DEBUG='0', M_FLOW_SECRET_KEY=secrets.token_urlsafe(40),
               MF_CHECK_PREFIX=PREFIX, MF_CHECK_PASSWORD=secrets.token_urlsafe(24), PYTHONUTF8='1')
    with tempfile.TemporaryDirectory(prefix='mflow-local-restore-') as directory:
        script = [sys.executable, str(Path(__file__).resolve())]
        subprocess.run([*script, 'seed'], env=dict(env, DATABASE_URL=source, DATABASE_URL_UNPOOLED=source), check=True)
        expected = fingerprint(source)
        archive = create_backup(source, directory)
        restore_verified_archive(target, archive)
        subprocess.run([*script, 'verify'], env=dict(env, DATABASE_URL=target, DATABASE_URL_UNPOOLED=target,
                                                       MF_CHECK_FINGERPRINT=expected), check=True)
    print('PASS: dump, restore, row verification, application restart, assignment, history, defense, personal grade, access.')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        drill()
    elif sys.argv[1:] == ['seed']:
        seed(os.environ['DATABASE_URL'])
    elif sys.argv[1:] == ['verify']:
        verify(os.environ['DATABASE_URL'])
    else:
        raise SystemExit('Unknown restore drill step.')
