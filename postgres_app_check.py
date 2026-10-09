"""Application smoke checks on an already restored disposable database only."""
from contextlib import closing
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

import psycopg
from psycopg import sql
from postgres_backup import BackupError, REQUIRED_TABLES, file_hash
from postgres_restore_check import test_environment


def fingerprint(url):
    digest = hashlib.sha256()
    with psycopg.connect(url, connect_timeout=15) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            for table in sorted(REQUIRED_TABLES):
                cursor.execute(sql.SQL('SELECT * FROM public.{}').format(sql.Identifier(table)))
                rows = sorted(json.dumps(row, default=str, ensure_ascii=False) for row in cursor)
                digest.update(json.dumps([table, rows], ensure_ascii=False).encode('utf-8'))
    return digest.hexdigest()


def require(value):
    if not value:
        raise BackupError('Один из сценариев не прошёл проверку; подробности и данные не выводятся.')


def phase(mode):
    url = os.environ['DATABASE_URL']
    test_environment(url)  # BEFORE importing app (which initializes tables).
    before = fingerprint(url)
    import app as module
    module.app.config.update(TESTING=True)
    require(fingerprint(url) == before)
    student, teacher = module.app.test_client(), module.app.test_client()
    stranger = module.app.test_client()
    prefix, password = os.environ['MF_CHECK_PREFIX'], os.environ['MF_CHECK_PASSWORD']

    def get(client, path):
        return client.get(path, base_url='https://localhost')

    def post(client, path, data):
        get(client, '/settings')
        with client.session_transaction(base_url='https://localhost') as session:
            token = session.get('csrf_token')
        if not token:
            get(client, '/login')
            with client.session_transaction(base_url='https://localhost') as session:
                token = session['csrf_token']
        return client.post(path, data=dict(data, csrf_token=token), base_url='https://localhost')

    def user_id(client):
        with client.session_transaction(base_url='https://localhost') as session:
            return session.get('user_id')

    for client, role in ((student, 'student'), (teacher, 'teacher'), (stranger, 'other')):
        route = '/teacher' if role == 'teacher' else ''
        name = prefix + '_' + role
        if mode == 'create':
            require(post(client, route + '/register', dict(username=name, password=password,
                    repeat_password=password)).status_code == 302)
            require(user_id(client) is not None)
            require(post(client, '/logout', {}).status_code == 302)
        require(post(client, route + '/login', dict(username=name, password=password)).status_code == 302)
        require(user_id(client) is not None)
    sid = user_id(student)
    if mode == 'create':
        require(post(student, '/add_project', dict(name=prefix)).status_code == 302)
    with closing(module.get_db()) as conn:
        project = conn.execute('SELECT id FROM projects WHERE name=? AND owner_id=?', (prefix, sid)).fetchone()
    require(project is not None)
    pid = project[0]
    base = f'/project/{pid}'
    if mode == 'create':
        require(post(student, base + '/add_member', dict(username=prefix + '_teacher')).status_code == 302)
        require(post(student, base + '/add', dict(title=prefix + '_task', assignee_id=str(sid))).status_code == 302)
        with closing(module.get_db()) as conn:
            tid = conn.execute('SELECT id FROM tasks WHERE project_id=?', (pid,)).fetchone()[0]
        require(post(student, base + f'/task/{tid}/update', dict(body='Проверка восстановления')).status_code == 302)
        for number in range(1, 6):
            fields = dict(number=str(number), revision='0', action='submit', result='Тестовый результат')
            if number == 5:
                fields['presentation_url'] = 'https://example.com/slides'
            require(post(student, base + '/stages', fields).status_code == 302)
            require(post(teacher, base + '/stages', dict(number=str(number), revision='1', action='accept')).status_code == 302)
        require(post(student, base + '/defense', dict(revision='0', action='submit',
                video_url='https://example.com/video', description='Тестовая защита')).status_code == 302)
        require(post(teacher, base + '/defense', dict(revision='1', action='accept', teacher_comment='Принято')).status_code == 302)
        from grading import CRITERIA
        scores = {f'criterion_{key}': str(maximum) for key, _, maximum in CRITERIA}
        require(post(teacher, base + '/assessment', dict(scores, student_id=str(sid),
                student_name='Тестовый ученик', note='Проверка', action='published')).status_code == 302)
    for client in (student, teacher):
        for suffix in ('', '/stages', '/defense', '/assessment', '/team'):
            require(get(client, base + suffix).status_code == 200)
    require(get(teacher, '/teacher').status_code == 200)
    require('45 / 45' in get(student, base + '/assessment').text)
    require(get(stranger, base).status_code == 403)
    require(post(teacher, '/add_project', dict(name=prefix + '_forbidden')).status_code == 403)
    require(post(student, base + '/assessment', dict(student_id=str(sid))).status_code == 403)
    require(student.post('/add_project', data={'name': prefix + '_no_csrf'}, base_url='https://localhost').status_code == 400)
    with closing(module.get_db()) as conn:
        require(conn.execute('SELECT COUNT(*) FROM project_stages WHERE project_id=? AND state=?', (pid, 'done')).fetchone()[0] == 5)
        require(conn.execute('SELECT COUNT(*) FROM task_updates WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?)', (pid,)).fetchone()[0] == 1)
        require(conn.execute('SELECT state FROM project_defenses WHERE project_id=?', (pid,)).fetchone()[0] == 'accepted')
    result = fingerprint(url)
    if mode == 'verify':
        require(before == os.environ['MF_CHECK_FINGERPRINT'])
        require(result == before)
    print(json.dumps({'fingerprint': result}))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--phase':
        try:
            require(sys.argv[2] in ('create', 'verify'))
            phase(sys.argv[2])
            return 0
        except Exception:
            # Never expose app traceback, database records or connection secrets.
            return 1
    try:
        require(len(sys.argv) == 2 and sys.stdin.isatty() and sys.stderr.isatty())
        archive = Path(sys.argv[1]).resolve()
        require(archive.is_relative_to((Path.home() / 'MFlow-private-backups').resolve()))
        receipt = json.loads(archive.with_name('restore_verified.json').read_text(encoding='utf-8'))
        require(receipt['restore_tested'] and receipt['sha256'] == file_hash(archive))
        print('Вставьте direct URL тестовой ветки, Database: mflow_restore_check. Ввод скрыт.')
        url = getpass.getpass('Тестовый direct URL: ').strip()
        test_environment(url)
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(('PG', 'M_FLOW_', 'DATABASE_URL', 'MF_CHECK_'))}
        env.update(DATABASE_URL=url, DATABASE_URL_UNPOOLED=url, M_FLOW_DATABASE_BACKEND='postgres',
                   M_FLOW_DEBUG='0', M_FLOW_SECRET_KEY=secrets.token_urlsafe(40),
                   M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD='',
                   MF_CHECK_PREFIX='check_' + secrets.token_hex(5), MF_CHECK_PASSWORD=secrets.token_urlsafe(24),
                   PYTHONUTF8='1')
        try:
            for mode in ('create', 'verify'):
                print('Проверка сценариев...' if mode == 'create' else 'Повторный запуск отдельного процесса...')
                run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--phase', mode],
                                     env=env, capture_output=True, timeout=300)
                require(run.returncode == 0)
                env['MF_CHECK_FINGERPRINT'] = json.loads(run.stdout)['fingerprint']
            with archive.with_name('app_verified_' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json').open('x', encoding='utf-8') as stream:
                json.dump(dict(verified_utc=datetime.now(timezone.utc).isoformat(),
                               application_scenarios_tested=True, separate_process_restart_tested=True,
                               production_restart_tested=False, browser_visual_tested=False), stream, indent=2)
            print('Сценарии обеих ролей и повторный запуск проверены. Рабочая база и Render не изменены.')
        finally:
            for key in ('DATABASE_URL', 'DATABASE_URL_UNPOOLED', 'MF_CHECK_PASSWORD', 'M_FLOW_SECRET_KEY'):
                env.pop(key, None)
        return 0
    except (Exception, KeyboardInterrupt):
        print('Проверка не завершена. Рабочую базу не перезапускайте. Секреты и подробности ошибки не выводятся.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
