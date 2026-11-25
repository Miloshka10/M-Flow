"""Private defense view built from accepted stage results and saved snapshots."""
from html import escape
import json

from database import begin_project_write, execute_schema, insert_id
from deadlines import format_timestamp
from material_links import safe_web_url
from navigation import page_links
from project_criteria import STYLE, file_link
from project_stages import STAGES
from submission_attachments import attachment_metadata
from teacher_supervision import participant
from teamwork import hidden_token

PAGE_SIZE = 10


def initialize(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS project_passports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
        version INTEGER NOT NULL CHECK(version > 0), snapshot TEXT NOT NULL,
        created_by INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
        UNIQUE(project_id,version)
    )""")


def goal_fields(goal, accepted_topic):
    if goal and goal.strip():
        return goal, 'Цель из плана выступления'
    return accepted_topic, 'Согласованная тема'


def assemble(conn, project_id, name):
    results = conn.execute("""SELECT s.number,COALESCE(d.result,s.result) AS result,
            COALESCE(d.resource_url,s.presentation_url) AS presentation_url,d.id AS decision_id,
            h.id AS submission_id,h.attempt
        FROM project_stages s
        LEFT JOIN work_history d ON d.project_id=s.project_id AND d.kind='stage' AND d.number=s.number
            AND d.revision=s.revision AND d.event='accept'
        LEFT JOIN work_history h ON h.project_id=d.project_id AND h.kind='stage' AND h.number=d.number
            AND h.event='submit' AND h.attempt=d.attempt
        WHERE s.project_id=? AND s.state='done' ORDER BY s.number""", (project_id,)).fetchall()
    plan = conn.execute('SELECT goal FROM project_defense_plans WHERE project_id=?', (project_id,)).fetchone()
    topic = next((r['result'] for r in results if r['number'] == 1), '')
    goal, goal_label = goal_fields(plan['goal'] if plan else '', topic)
    files = attachment_metadata(conn, [r['submission_id'] for r in results if r['submission_id']])
    criteria = {}
    for row in conn.execute("""SELECT c.title,a.id AS file_id,a.filename,d.id AS decision_id,b.note AS evidence
        FROM project_criterion_reviews r JOIN project_criteria c ON c.id=r.criterion_id
        JOIN work_history d ON d.id=r.decision_id AND d.event='accept'
        JOIN project_stages s ON s.project_id=d.project_id AND s.number=d.number
            AND s.revision=d.revision AND s.state='done'
        LEFT JOIN submission_attachments a ON a.id=r.attachment_id
        LEFT JOIN project_criterion_responses b ON b.criterion_id=r.criterion_id AND b.submission_id=r.submission_id
        WHERE c.project_id=? AND r.status='confirmed' ORDER BY c.id""", (project_id,)):
        criteria.setdefault(row['decision_id'], []).append(
            dict(title=row['title'], file_id=row['file_id'], filename=row['filename'], evidence=row['evidence'] or ''))
    stages = [dict(number=r['number'], result=r['result'], attempt=r['attempt'],
                   presentation=r['presentation_url'] if r['number'] == 5 else '',
                   files=[dict(id=f['id'], filename=f['filename']) for f in files.get(r['submission_id'], ())],
                   criteria=criteria.get(r['decision_id'], [])) for r in results]
    return dict(name=name, goal=goal, goal_label=goal_label, stages=stages)


def save(conn, project_id, user):
    begin_project_write(conn, project_id)
    try:
        if user['role'] != 'student' or not participant(conn, project_id, user['id']):
            raise PermissionError('Версии для защиты сохраняют ученики проекта.')
        name = conn.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()['name']
        current = assemble(conn, project_id, name)
        if not current['stages']:
            raise ValueError('Версию можно сохранить после принятия первого этапа.')
        encoded = json.dumps(current, ensure_ascii=False)
        latest = conn.execute('''SELECT id,version,snapshot FROM project_passports
            WHERE project_id=? ORDER BY version DESC LIMIT 1''', (project_id,)).fetchone()
        if latest and latest['snapshot'] == encoded:
            conn.commit()
            return latest['id'], False
        version = latest['version'] + 1 if latest else 1
        passport_id = insert_id(conn, '''INSERT INTO project_passports(project_id,version,snapshot,created_by)
            VALUES(?,?,?,?)''', (project_id, version, encoded, user['id']))
        conn.commit()
        return passport_id, True
    except Exception:
        conn.rollback()
        raise


def versions(conn, project_id, page):
    return conn.execute('''SELECT id,version,created_at FROM project_passports
        WHERE project_id=? ORDER BY version DESC LIMIT ? OFFSET ?''',
                        (project_id, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)).fetchall()


def saved_version(conn, project_id, passport_id):
    return conn.execute('SELECT * FROM project_passports WHERE project_id=? AND id=?',
                        (project_id, passport_id)).fetchone()


def render(project_id, data, token, student, history, page, saved=None):
    base = f'/project/{project_id}/passport'
    stages = data['stages']
    heading = f'<small>Версия {saved["version"]} · {format_timestamp(saved["created_at"])}</small>' if saved else ''
    goal = (f'<section><h2>{escape(data["goal_label"])}</h2><p class="criterion-note">{escape(data["goal"])}</p></section>'
            if data['goal'] else '')
    presentation = next((r['presentation'] for r in stages if r['presentation']), '')
    primary = (f'<a class="btn" href="{escape(presentation, quote=True)}" target="_blank" rel="noopener noreferrer">'
               'Открыть презентацию</a>' if safe_web_url(presentation) else '')
    if saved:
        primary += f'<a class="action-link" href="{base}">Открыть текущий паспорт</a>'
    elif student and stages:
        primary += f'<form method="post" action="{base}">{hidden_token(token)}<button name="action" value="save">Сохранить версию для защиты</button></form>'
    cards = []
    titles = dict(STAGES)
    for row in stages:
        number = row['number']
        files = [f'<li>{file_link(project_id, f["id"], f["filename"])}</li>' for f in row['files']]
        confirmed = []
        for item in row['criteria']:
            source = file_link(project_id, item['file_id'], item['filename'])
            detail = f'<p class="criterion-note">{escape(item["evidence"])}</p>' if item.get('evidence') else ''
            confirmed.append(f'<li><strong>{escape(item["title"])}</strong>{f" · {source}" if source else ""}{detail}</li>')
        evidence = f'<h4>Подтверждённые критерии</h4><ul>{"".join(confirmed)}</ul>' if confirmed else ''
        originals = f'<h4>Файлы принятой сдачи</h4><ul>{"".join(files)}</ul>' if files else ''
        attempt = f' · Сдача №{row["attempt"]}' if row['attempt'] else ''
        result = (f'<p class="criterion-note">{escape(row["result"] or "Описание не указано.")}</p>'
                  if number != 1 or row['result'] != data['goal'] else '')
        cards.append(f'''<article class="passport-stage"><header><small>Этап {number}{attempt}</small>
            <span class="criterion-state" data-criterion-state="confirmed">Принят</span></header>
            <h3>{escape(titles[number])}</h3>{result}
            {evidence}{originals}</article>''')
    rows = ''.join(f'<li><a href="{base}/{r["id"]}">Версия {r["version"]}</a> <small>{format_timestamp(r["created_at"])}</small></li>'
                   for r in history[:PAGE_SIZE])
    version_list = (f'<section class="criterion-panel" id="passport-versions"><h2>Сохранённые версии</h2><ul>{rows}</ul>'
                    + page_links(base, page, len(history) > PAGE_SIZE, fragment='#passport-versions') + '</section>') if not saved and (rows or page > 1) else ''
    accepted = f'<section class="passport-results"><h2>Принятые результаты</h2>{"".join(cards)}</section>' if cards else (
        '<section class="criterion-panel"><h2>Принятые результаты</h2><p>Здесь появятся результаты после проверки этапов учителем.</p></section>')
    return STYLE + f'''<section class="passport-cover"><header>{heading}<h2>{escape(data["name"])}</h2>
        <p>Принято {len(stages)} из 5 этапов</p></header>{goal}<div class="criterion-actions">{primary}</div></section>
        {accepted}{version_list}'''
