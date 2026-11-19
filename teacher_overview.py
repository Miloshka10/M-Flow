"""Read models for the teacher cabinet and review center."""
from collections import defaultdict
from datetime import timedelta

from deadlines import deadline_info, deadline_text
from project_stages import stages_from_rows, stage_summary
from video_defense import DEFENSE_STATES

SCOPE = """WITH visible_projects AS (
    SELECT p.id FROM projects p WHERE (p.owner_id=? OR EXISTS (
        SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?)){class_filter}
) """
CLASS_FILTER = """ AND EXISTS (
    SELECT 1 FROM learning_group_members gm JOIN learning_groups g ON g.id=gm.group_id
    WHERE gm.group_id=? AND g.teacher_id=? AND gm.student_id=p.owner_id AND (
        gm.project_id=p.id OR EXISTS (
            SELECT 1 FROM group_assignment_projects ap JOIN group_assignments a ON a.id=ap.assignment_id
            WHERE ap.project_id=p.id AND ap.student_id=gm.student_id AND a.group_id=g.id)))"""
STUDENTS = """SELECT m.project_id,u.id,u.username FROM project_members m
    JOIN visible_projects v ON v.id=m.project_id JOIN users u ON u.id=m.user_id
    WHERE u.role='student' ORDER BY u.username,u.id"""
ATTENTION_LIMIT = 6


def review_groups(conn, teacher_id, group_id=None):
    return conn.execute("""SELECT id,name,archived FROM learning_groups
        WHERE teacher_id=? AND (archived=0 OR id=?) ORDER BY archived,name,id""",
        (teacher_id, group_id)).fetchall()


def project_scope(teacher_id, group_id=None):
    if group_id is None:
        return SCOPE.format(class_filter=''), (teacher_id, teacher_id)
    return SCOPE.format(class_filter=CLASS_FILTER), (teacher_id, teacher_id, group_id, teacher_id)


def load_teacher_projects(conn, teacher_id, today, *, cabinet=False, reports=False, report_project=None, group_id=None):
    scope, params = project_scope(teacher_id, group_id)

    def grouped(query, extra=()):
        result = defaultdict(list)
        for row in conn.execute(scope + query, (*params, *extra)):
            result[row['project_id']].append(dict(row))
        return result

    projects = conn.execute(scope + """SELECT p.id,p.name,p.owner_id FROM projects p
        JOIN visible_projects v ON v.id=p.id ORDER BY p.id DESC""", params).fetchall()
    students = grouped(STUDENTS)
    stages = grouped("""SELECT s.project_id,s.number,s.state,s.revision,s.updated_at,s.reviewed_by,u.username AS author_name
        FROM project_stages s JOIN visible_projects v ON v.id=s.project_id
        LEFT JOIN users u ON u.id=s.submitted_by""")
    defenses = grouped("""SELECT d.project_id,d.state,d.revision,d.updated_at,u.username AS author_name
        FROM project_defenses d JOIN visible_projects v ON v.id=d.project_id
        LEFT JOIN users u ON u.id=d.author_id""")
    reports_by_project = {}
    if reports:
        where = '' if report_project is None else ' WHERE t.project_id=?'
        reports_by_project = grouped("""SELECT t.project_id,t.title AS task_title,r.id,r.task_id,r.created_at,u.username
            FROM tasks t JOIN visible_projects v ON v.id=t.project_id
            JOIN task_updates r ON r.id=(SELECT MAX(latest.id) FROM task_updates latest WHERE latest.task_id=t.id)
            JOIN users u ON u.id=r.user_id""" + where, () if report_project is None else (report_project,))
    totals = grouped("""SELECT t.project_id,t.assignee_id,t.status,
            CASE WHEN t.status='done' THEN NULL ELSE t.deadline END AS deadline,COUNT(*) AS amount
        FROM tasks t JOIN visible_projects v ON v.id=t.project_id
        GROUP BY t.project_id,t.assignee_id,t.status,CASE WHEN t.status='done' THEN NULL ELSE t.deadline END""") if cabinet else {}

    overview = []
    for project in projects:
        pid = project['id']
        completed, current = stage_summary(stages_from_rows(stages[pid]))
        defense = next(iter(defenses[pid]), {'revision': 0})
        row = dict(id=pid, name=project['name'], completed_stages=completed, current_stage=current,
                   defense=defense, defense_state=DEFENSE_STATES[defense['state']] if defense['revision'] else 'Запись ещё не добавлена',
                   needs_review=bool(current and current['state'] == 'review') or defense.get('state') == 'submitted',
                   legacy_owner=project['owner_id'] == teacher_id, can_manage=False,
                   students=[dict(id=s['id'], name=s['username']) for s in students[pid]],
                   reports=[dict(task_id=r['task_id'], task_title=r['task_title'], report=r)
                            for r in reports_by_project.get(pid, ())])
        if cabinet:
            add_task_totals(row, totals.get(pid, ()), today)
        overview.append(row)
    return overview


def add_task_totals(project, groups, today):
    students = {s['id']: s for s in project['students']}
    for student in students.values():
        student.update(total=0, done=0, overdue=0)
    counts = dict(todo=0, progress=0, done=0, overdue=0, soon=0, unassigned=0)
    total = 0
    for row in groups:
        amount = row['amount']
        total += amount
        status = row['status'] if row['status'] in ('todo', 'progress', 'done') else 'todo'
        counts[status] += amount
        student = students.get(row['assignee_id'])
        if student is not None:
            student['total'] += amount
        if status == 'done':
            if student is not None:
                student['done'] += amount
            continue
        due = deadline_info(row['deadline'], status, today)
        if due['state'] == 'overdue':
            counts['overdue'] += amount
            if student is not None:
                student['overdue'] += amount
        elif due['active'] and due['days'] <= 3:
            counts['soon'] += amount
        if student is None:
            counts['unassigned'] += amount
    attention = bool(counts['overdue'] or counts['soon'] or counts['unassigned'])
    health = ('overdue' if counts['overdue'] else 'attention' if attention or project['needs_review'] else
              'empty' if not total else 'done' if counts['done'] == total else 'normal')
    project.update(counts=counts, total=total, health=health, has_attention=attention, attention=[])


def load_attention(conn, teacher_id, projects, today):
    # One batched projection of actionable tasks only. Apply the same calendar
    # validation as task pages because older records may contain invalid dates.
    by_id = {p['id']: p for p in projects if p['has_attention']}
    if not by_id:
        return
    scope, params = project_scope(teacher_id)
    rows = conn.execute(scope + """SELECT t.project_id,t.id,t.status,t.title,t.deadline,t.priority,u.username AS assignee_name
        FROM tasks t JOIN visible_projects v ON v.id=t.project_id
        LEFT JOIN project_members m ON m.project_id=t.project_id AND m.user_id=t.assignee_id
        LEFT JOIN users u ON u.id=m.user_id AND u.role='student'
        WHERE t.status<>'done' AND (u.id IS NULL OR (t.deadline<>'' AND t.deadline<=?))
        ORDER BY t.project_id,CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
            WHEN 'normal' THEN 3 WHEN 'low' THEN 4 ELSE 5 END,t.id""",
        (*params, (today + timedelta(days=3)).isoformat()))
    for task in rows:
        project = by_id.get(task['project_id'])
        if project is None:
            continue
        info = deadline_info(task['deadline'], task['status'], today)
        reasons = [deadline_text(info)] if info['active'] and info['days'] <= 3 else []
        if task['assignee_name'] is None:
            reasons.append('нет ответственного')
        if reasons:
            attention = project['attention']
            # Store at most six rows per project, also sufficient for the global top six.
            attention.append(dict(task=dict(task), reasons=reasons, sort=info['days'] if info['days'] is not None else 9999))
            attention.sort(key=lambda item: item['sort'])
            del attention[ATTENTION_LIMIT:]
