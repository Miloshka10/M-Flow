"""Manual browser QA on a throwaway SQLite database, never production."""
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch
from werkzeug.security import generate_password_hash


def main():
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
            conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(1,'Школьная метеостанция',2)")
            conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(1,?)', [(1,), (2,)])
            from notifications import notify
            notify(conn, 1, 'preview:submit', 'Этап 1 отправлен на проверку', '', '/project/1/stages', [1])
            notify(conn, 1, 'preview:review', 'Этап 1: нужна доработка',
                   'Уточните, какие измерения будет выполнять метеостанция.', '/project/1/stages', [2])
            conn.commit()
            conn.close()
            module.app.run(host='127.0.0.1', port=5091, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
