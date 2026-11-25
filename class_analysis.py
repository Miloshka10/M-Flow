"""Durable queue for teacher-requested analyses of exact class submissions."""
from contextlib import closing
import logging
import time
from uuid import uuid4

from database import PostgresConnection, begin_project_write, execute_schema, form_database_id
from project_criteria import for_stage
from project_stages import STAGES, STAGE_DESCRIPTIONS
import submission_analysis as analysis
from submission_attachments import attachment_metadata

LABELS = {'queued': 'В очереди', 'running': 'Идёт разбор', 'done': 'Разбор готов',
          'failed': 'Ошибка разбора', 'stale': 'Сдача изменилась'}
LEASE_SECONDS = 300

# Use the same scope for listing, accepting a selection and validating a worker result.
ELIGIBLE = """ FROM group_assignments a JOIN learning_groups g ON g.id=a.group_id
    JOIN users teacher ON teacher.id=g.teacher_id AND teacher.role='teacher'
    JOIN group_assignment_projects ap ON ap.assignment_id=a.id
    JOIN learning_group_members m ON m.group_id=g.id AND m.student_id=ap.student_id
    JOIN projects p ON p.id=ap.project_id AND p.owner_id=m.student_id
    JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=g.teacher_id
    JOIN project_stages s ON s.project_id=p.id AND s.state='review'
    JOIN work_history h ON h.project_id=p.id AND h.number=s.number AND h.revision=s.revision
        AND h.kind='stage' AND h.event='submit'
    LEFT JOIN class_analysis_jobs j ON j.assignment_id=a.id AND j.teacher_id=g.teacher_id AND j.history_id=h.id
    WHERE a.id=? AND g.teacher_id=? AND g.archived=0"""


def initialize(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS class_analysis_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id INTEGER NOT NULL REFERENCES group_assignments(id),
        teacher_id INTEGER NOT NULL REFERENCES users(id), history_id INTEGER NOT NULL REFERENCES work_history(id),
        model TEXT NOT NULL, state TEXT NOT NULL, token TEXT NOT NULL DEFAULT '',
        expires INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        UNIQUE (assignment_id,teacher_id,history_id))""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_class_analysis_pending ON class_analysis_jobs(state,expires,id)')


def available(conn, assignment_id, teacher_id):
    rows = conn.execute('SELECT p.id AS project_id,h.id AS history_id,s.number,j.state' + ELIGIBLE,
                        (assignment_id, teacher_id))
    return {(r['project_id'], r['number']): dict(r) for r in rows}


def enqueue(conn, assignment_id, teacher_id, selections, model):
    if not model:
        raise ValueError('Для разбора требуется настроенная модель.')
    if not 1 <= len(selections) <= 30:
        raise ValueError('Выберите от одной до 30 сдач.')
    ids = {form_database_id(value) for value in selections}
    allowed = {r['history_id'] for r in available(conn, assignment_id, teacher_id).values()}
    if not ids <= allowed:
        raise ValueError('Список сдач изменился. Обновите страницу задания.')
    for history_id in sorted(ids):
        conn.execute("""INSERT INTO class_analysis_jobs(assignment_id,teacher_id,history_id,model,state)
            VALUES(?,?,?,?,'queued') ON CONFLICT(assignment_id,teacher_id,history_id) DO UPDATE
            SET state='queued',model=excluded.model,attempts=0,expires=0,token=''
            WHERE class_analysis_jobs.state IN ('failed','stale')""", (assignment_id, teacher_id, history_id, model))
    conn.commit()


def claim(conn):
    postgres = isinstance(conn, PostgresConnection)
    if not postgres:
        conn.execute('BEGIN IMMEDIATE')
    now = int(time.time())
    conn.execute("""UPDATE class_analysis_jobs SET state='failed',token='' WHERE state='running'
        AND expires<=? AND attempts>=3""", (now,))
    row = conn.execute("""SELECT * FROM class_analysis_jobs WHERE state='queued' OR
        (state='running' AND expires<=?) ORDER BY id LIMIT 1""" +
        (' FOR UPDATE SKIP LOCKED' if postgres else ''), (now,)).fetchone()
    if row is None:
        conn.commit()
        return None
    job = dict(row)
    job['token'] = uuid4().hex
    conn.execute("""UPDATE class_analysis_jobs SET state='running',token=?,expires=?,attempts=attempts+1
        WHERE id=?""", (job['token'], now + LEASE_SECONDS, job['id']))
    conn.commit()
    return job


def eligible(conn, job):
    return conn.execute('SELECT p.id' + ELIGIBLE + ' AND h.id=?',
                        (job['assignment_id'], job['teacher_id'], job['history_id'])).fetchone()


def work_once(connect):
    with closing(connect()) as conn:
        job = claim(conn)
    if job is None:
        return False
    history, teacher = job['history_id'], job['teacher_id']
    state, report, project_id = 'stale', None, None
    try:
        with closing(connect()) as conn:
            snapshot = analysis.current_submission(conn, history, teacher)
            if snapshot and eligible(conn, job):
                project_id = snapshot['project_id']
                number = snapshot['number']
                files = analysis.file_inputs(conn, history, attachment_metadata(conn, [history]).get(history, []))
                terms = analysis.assignment_terms(conn, project_id, teacher)
                titles = tuple(r['title'] for r in for_stage(conn, project_id, number))
        if project_id is not None:
            report = analysis.analyze(job['model'], snapshot, files, dict(STAGES)[number], STAGE_DESCRIPTIONS[number],
                                      terms, stage_criteria=titles)
            state = 'done'
    except analysis.AnalysisUnavailable:
        state = 'failed'
    except Exception:
        logging.exception('Class analysis job %s failed', job['id'])
        state = 'failed'
    with closing(connect()) as conn:
        if project_id is not None:
            begin_project_write(conn, project_id)
        if state == 'done' and not eligible(conn, job):
            state = 'stale'
        updated = conn.execute("""UPDATE class_analysis_jobs SET state=?,expires=0,token=''
            WHERE id=? AND state='running' AND token=?""", (state, job['id'], job['token']))
        if updated.rowcount and state == 'done':
            analysis.save_report(conn, history, teacher, job['model'], report)
        conn.commit()
    return True


if __name__ == '__main__':
    import argparse
    from app import get_db
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Process at most one available job and exit.')
    if parser.parse_args().once:
        work_once(get_db)
    else:
        while True:
            if not work_once(get_db):
                time.sleep(2)
