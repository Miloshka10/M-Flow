"""PostgreSQL dump/restore drill on synthetic databases.

Requires PostgreSQL 18 tools and empty loopback databases mflow_drill_source
and mflow_restore_check. Supply their direct URLs in M_FLOW_DRILL_SOURCE_URL
and M_FLOW_DRILL_TARGET_URL. The caller owns and cleans up both databases.
"""
from contextlib import closing
from datetime import date, timedelta
from io import BytesIO
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import psycopg
from werkzeug.datastructures import FileStorage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postgres_app_check import fingerprint, phase
from postgres_backup import create_backup, table_row_hashes
from postgres_restore_check import require_empty, restore_verified_archive
from submission_attachments import read_uploads, save_attachments


PREFIX = 'classroom_drill'
ATTACHMENT = b'mflow synthetic attachment\x00\xff'


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
        submission = conn.execute("""SELECT id FROM work_history WHERE project_id=?
            AND kind='stage' AND number=1 AND event='submit' ORDER BY revision DESC LIMIT 1""", (project,)).fetchone()
        save_attachments(conn, submission['id'], read_uploads([FileStorage(BytesIO(ATTACHMENT), 'report.bin')]))
        conn.commit()


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
        file_id = conn.execute("""SELECT a.id FROM submission_attachments a JOIN work_history h ON h.id=a.history_id
            WHERE h.project_id=? AND a.filename='report.bin'""", (work['id'],)).fetchone()['id']
    base = f'/project/{work["id"]}'
    file_url = f'{base}/attachments/{file_id}'
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
            assert client.get(file_url).status_code == 404
            continue
        assert client.get(file_url).data == ATTACHMENT
        assert 'Учебная проверка' in client.get(assignment).text
        assert 'Тестовый результат' in client.get(base + '/history').text
        assert 'Защита принята' in client.get(base + '/defense').text
        if role == 'student':
            assert '45 / 45' in client.get(base + '/assessment').text
            with client.session_transaction() as session:
                token = session['csrf_token']
            new_name = 'Проект после восстановления'
            created = client.post('/add_project', data={'name': new_name, 'csrf_token': token})
            with closing(app.get_db()) as conn:
                new_project = conn.execute("""SELECT new.id FROM projects AS new
                    JOIN projects AS original ON original.owner_id=new.owner_id
                    WHERE original.id=? AND new.name=?""", (work['id'], new_name)).fetchone()
            if new_project is None or created.status_code != 302:
                raise AssertionError('Could not create a project for the restored student.')
            assert new_name in client.get(created.location).text
            assert new_name in client.get(f'/project/{new_project["id"]}').text
        else:
            assert 'Этапы и защита приняты: 1' in client.get(assignment).text

    # Change a synthetic submission without adding or removing a row.
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
        with conn.transaction(force_rollback=True):
            tables = {'project_stages'}
            original_hashes = table_row_hashes(conn, tables)
            changed = conn.execute("""UPDATE project_stages
                SET result = %s WHERE project_id = %s AND number = 1""",
                ('Changed after restore', work['id'])).rowcount
            if changed != 1 or table_row_hashes(conn, tables) == original_hashes:
                raise AssertionError('Row hashes failed to detect a modified submission.')
            tables = {'submission_attachments'}
            original_hashes = table_row_hashes(conn, tables)
            conn.execute('UPDATE submission_attachments SET content=%s WHERE id=%s', (ATTACHMENT[::-1], file_id))
            if table_row_hashes(conn, tables) == original_hashes:
                raise AssertionError('Row hashes failed to detect modified attachment bytes.')


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
        receipt = restore_verified_archive(target, archive)
        if receipt['row_contents_match'] is not True:
            raise AssertionError('Restoration did not verify the source row contents.')
        subprocess.run([*script, 'verify'], env=dict(env, DATABASE_URL=target, DATABASE_URL_UNPOOLED=target,
                                                       MF_CHECK_FINGERPRINT=expected), check=True)
    print('PASS: dump, restore, row verification, altered submission detection, application restart, '
          'assignment, history, defense, personal grade, access, new project creation.')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        drill()
    elif sys.argv[1:] == ['seed']:
        seed(os.environ['DATABASE_URL'])
    elif sys.argv[1:] == ['verify']:
        verify(os.environ['DATABASE_URL'])
    else:
        raise SystemExit('Unknown restore drill step.')
