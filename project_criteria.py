"""Project criteria, evidence and feedback attached to the version under review."""
from hashlib import sha256
from html import escape

from database import begin_project_write, execute_schema, form_database_id
from navigation import stage_feedback_url
from submission_attachments import FILE_COUNT
from teacher_supervision import participant
from teamwork import hidden_token

MAX_CRITERIA = 5
TITLE_LIMIT = 180
NOTE_LIMIT = 1000
DECISIONS = {'confirmed': 'Подтверждено', 'revise': 'Нужна доработка'}
STYLE = '<link rel="stylesheet" href="/static/project-criteria.css?v=2">'


def initialize(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS project_criteria (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
        number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 5), title TEXT NOT NULL,
        draft_note TEXT NOT NULL DEFAULT ''
    )""")
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS project_criterion_responses (
        criterion_id INTEGER NOT NULL REFERENCES project_criteria(id),
        submission_id INTEGER NOT NULL REFERENCES work_history(id),
        note TEXT NOT NULL, attachment_id INTEGER REFERENCES submission_attachments(id),
        PRIMARY KEY(criterion_id, submission_id)
    )""")
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS project_criterion_reviews (
        criterion_id INTEGER NOT NULL REFERENCES project_criteria(id),
        decision_id INTEGER NOT NULL REFERENCES work_history(id),
        submission_id INTEGER NOT NULL REFERENCES work_history(id),
        status TEXT NOT NULL CHECK(status IN ('confirmed','revise')),
        note TEXT NOT NULL, attachment_id INTEGER REFERENCES submission_attachments(id),
        PRIMARY KEY(criterion_id, decision_id)
    )""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_project_criteria ON project_criteria(project_id,number,id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_criterion_submission ON project_criterion_responses(submission_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_criterion_decision ON project_criterion_reviews(decision_id)')


def for_stage(conn, project_id, number):
    return conn.execute('SELECT * FROM project_criteria WHERE project_id=? AND number=? ORDER BY id',
                        (project_id, number)).fetchall()


def submitted_history_id(conn, project_id, stage):
    row = conn.execute("""SELECT id FROM work_history WHERE project_id=? AND kind='stage'
        AND number=? AND revision=? AND event='submit'""", (project_id, stage['number'], stage['revision'])).fetchone()
    return row['id'] if row else None


def configure(conn, project_id, user, form):
    begin_project_write(conn, project_id)
    try:
        if user['role'] != 'teacher' or not participant(conn, project_id, user['id']):
            raise PermissionError('Критерии задаёт учитель проекта.')
        if form.get('number') not in ('1', '2', '3', '4', '5'):
            raise ValueError('Выберите этап проекта.')
        number = int(form['number'])
        stage = conn.execute('SELECT number,state,revision FROM project_stages WHERE project_id=? AND number=?',
                             (project_id, number)).fetchone()
        if stage and stage['state'] == 'done':
            raise ValueError('Решение по этому этапу уже сохранено. Выберите следующий этап.')
        if stage and stage['state'] == 'review' and not submitted_history_id(conn, project_id, stage):
            raise ValueError('Добавьте критерии к этому этапу после следующей сдачи.')
        action = form.get('action')
        rows = for_stage(conn, project_id, number)
        criterion_id = None
        if action in ('rename', 'remove'):
            criterion_id = form_database_id(form.get('criterion_id'))
            target = next((r for r in rows if r['id'] == criterion_id), None)
            if not target:
                raise ValueError('Критерий больше не доступен. Обновите страницу.')
            if form.get('original_title') != target['title']:
                raise ValueError('Название уже изменилось. Обновите страницу.')
            used = conn.execute("""SELECT 1 FROM project_criterion_responses WHERE criterion_id=?
                UNION ALL SELECT 1 FROM project_criterion_reviews WHERE criterion_id=? LIMIT 1""",
                                (criterion_id, criterion_id)).fetchone()
            if used or target['draft_note']:
                raise ValueError('По этому критерию уже есть ответ или решение. Его название сохранено в истории.')
        if action == 'remove':
            conn.execute('DELETE FROM project_criteria WHERE id=?', (criterion_id,))
        elif action in ('add', 'rename'):
            title = ' '.join(form.get('title', '').split())
            if not 1 <= len(title) <= TITLE_LIMIT:
                raise ValueError(f'Введите название критерия длиной до {TITLE_LIMIT} символов.')
            if any(r['id'] != criterion_id and r['title'].casefold() == title.casefold() for r in rows):
                raise ValueError('На этом этапе уже есть критерий с таким названием.')
            if action == 'add':
                if len(rows) >= MAX_CRITERIA:
                    raise ValueError(f'На одном этапе можно задать до {MAX_CRITERIA} критериев.')
                conn.execute('INSERT INTO project_criteria(project_id,number,title) VALUES(?,?,?)',
                             (project_id, number, title))
            else:
                conn.execute('UPDATE project_criteria SET title=? WHERE id=?', (title, criterion_id))
        else:
            raise ValueError('Выберите действие с критерием.')
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _validate_fields(form, criteria, prefixes):
    allowed = {f'{prefix}{r["id"]}' for r in criteria for prefix in prefixes}
    version = form.get('criteria_version')
    if ((version is not None and version != _version(criteria)) or
            any(key.startswith(prefixes) and key not in allowed for key in form)):
        raise ValueError('Критерии изменились. Откройте актуальную форму этапа.')


def _version(criteria):
    return sha256(repr([(r['id'], r['title']) for r in criteria]).encode('utf-8')).hexdigest()


def version_field(criteria):
    return f'<input type="hidden" name="criteria_version" value="{_version(criteria)}">'


def response_values(form, criteria, uploads, action):
    _validate_fields(form, criteria, ('evidence_note_', 'evidence_file_'))
    values = []
    for criterion in criteria:
        cid = criterion['id']
        key = f'evidence_note_{cid}'
        if action == 'save' and key not in form:
            continue
        note = form.get(key, criterion['draft_note']).strip()
        if len(note) > NOTE_LIMIT:
            raise ValueError(f'Пояснение к критерию может содержать до {NOTE_LIMIT} символов.')
        position = form.get(f'evidence_file_{cid}', '') if action == 'submit' else ''
        if position and position not in {str(n) for n in range(1, len(uploads) + 1)}:
            raise ValueError('Приложите файл, выбранный для критерия, или измените выбор файла.')
        filename = uploads[int(position) - 1][0] if position else None
        values.append((cid, note, filename))
    return values


def save_responses(conn, history_id, values):
    if not values:
        return
    if history_id is None:
        conn.executemany('UPDATE project_criteria SET draft_note=? WHERE id=?',
                         [(note, cid) for cid, note, _ in values])
        return
    originals = {r['filename']: r['id'] for r in conn.execute(
        'SELECT id,filename FROM submission_attachments WHERE history_id=?', (history_id,))} if any(
            filename for _, _, filename in values) else {}
    responses = [(cid, history_id, note, originals[filename] if filename else None)
                 for cid, note, filename in values if note or filename]
    if responses:
        conn.executemany("""INSERT INTO project_criterion_responses(criterion_id,submission_id,note,attachment_id)
            VALUES(?,?,?,?)""", responses)
    conn.executemany("UPDATE project_criteria SET draft_note='' WHERE id=?", [(cid,) for cid, _, _ in values])


def decision_values(conn, form, criteria, submission_id, action):
    _validate_fields(form, criteria, ('criterion_status_', 'criterion_note_', 'criterion_file_'))
    if not criteria or submission_id is None:
        return []
    originals = {str(row['id']): row['id'] for row in conn.execute(
        'SELECT id FROM submission_attachments WHERE history_id=?', (submission_id,))}
    chosen = {r['criterion_id']: r['attachment_id'] for r in conn.execute(
        'SELECT criterion_id,attachment_id FROM project_criterion_responses WHERE submission_id=?', (submission_id,))}
    decisions = []
    for criterion in criteria:
        cid = criterion['id']
        status = form.get(f'criterion_status_{cid}', '')
        note = form.get(f'criterion_note_{cid}', '').strip()
        if action == 'accept' and status != 'confirmed':
            raise ValueError(f'Перед принятием подтвердите критерий «{criterion["title"]}».')
        if not status and not note:
            continue
        if status not in DECISIONS:
            raise ValueError(f'Выберите решение для критерия «{criterion["title"]}».')
        if len(note) > NOTE_LIMIT or (status == 'revise' and not note):
            raise ValueError(f'Укажите, что исправить в критерии «{criterion["title"]}». До {NOTE_LIMIT} символов.')
        selected = form.get(f'criterion_file_{cid}', '')
        if selected and selected not in originals:
            raise ValueError('Выберите файл из проверяемой сдачи.')
        decisions.append((cid, submission_id, status, note, originals[selected] if selected else chosen.get(cid)))
    return decisions


def save_decisions(conn, decision_id, decisions):
    if decisions:
        conn.executemany("""INSERT INTO project_criterion_reviews
            (decision_id,criterion_id,submission_id,status,note,attachment_id) VALUES(?,?,?,?,?,?)""",
                         [(decision_id, *decision) for decision in decisions])


def load(conn, project_id):
    """Latest response and decision for each criterion, with each source preserved."""
    rows = conn.execute("""SELECT c.*, s.state AS stage_state, s.revision AS stage_revision,
            h.id AS submission_id, h.revision AS submission_revision, h.attempt,
            r.status, r.note AS feedback, r.submission_id AS reviewed_submission,
            decision.id AS decision_id, decision.event AS decision_event, decision.revision AS decision_revision,
            ra.id AS reviewed_file_id, ra.filename AS reviewed_filename,
            b.note AS response, ba.id AS response_file_id, ba.filename AS response_filename, ba.sha256 AS response_hash,
            old.id AS previous_submission_id, previous.note AS previous_response,
            pa.id AS previous_file_id, pa.filename AS previous_filename, pa.sha256 AS previous_hash,
            EXISTS(SELECT 1 FROM project_criterion_responses q WHERE q.criterion_id=c.id) AS has_response
        FROM project_criteria c
        LEFT JOIN project_stages s ON s.project_id=c.project_id AND s.number=c.number
        LEFT JOIN work_history h ON h.id=(
            SELECT last.id FROM work_history last WHERE last.project_id=c.project_id AND last.kind='stage'
                AND last.number=c.number AND last.event='submit' AND last.revision<=s.revision
                ORDER BY last.revision DESC LIMIT 1)
        LEFT JOIN project_criterion_responses b ON b.criterion_id=c.id AND b.submission_id=h.id
        LEFT JOIN submission_attachments ba ON ba.id=b.attachment_id
        LEFT JOIN project_criterion_reviews r ON r.criterion_id=c.id AND r.decision_id=(
            SELECT MAX(q.decision_id) FROM project_criterion_reviews q WHERE q.criterion_id=c.id)
        LEFT JOIN work_history decision ON decision.id=r.decision_id
        LEFT JOIN submission_attachments ra ON ra.id=r.attachment_id
        LEFT JOIN work_history old ON old.id=(
            SELECT last.id FROM work_history last WHERE last.project_id=c.project_id AND last.kind='stage'
                AND last.number=c.number AND last.event='submit' AND last.revision<h.revision
                ORDER BY last.revision DESC LIMIT 1)
        LEFT JOIN project_criterion_responses previous ON previous.criterion_id=c.id AND previous.submission_id=old.id
        LEFT JOIN submission_attachments pa ON pa.id=previous.attachment_id
        WHERE c.project_id=? ORDER BY c.number,c.id""", (project_id,))
    groups = {}
    for row in rows:
        groups.setdefault(row['number'], []).append(dict(row))
    return groups


def history_notes(conn, ids):
    if not ids:
        return {}
    marks = ','.join('?' for _ in ids)
    rows = conn.execute(f"""SELECT b.submission_id AS event_id,c.title,b.note,'' AS status,
            a.id AS file_id,a.filename FROM project_criterion_responses b
        JOIN project_criteria c ON c.id=b.criterion_id LEFT JOIN submission_attachments a ON a.id=b.attachment_id
        WHERE b.submission_id IN ({marks})
        UNION ALL SELECT r.decision_id,c.title,r.note,r.status,a.id,a.filename FROM project_criterion_reviews r
        JOIN project_criteria c ON c.id=r.criterion_id LEFT JOIN submission_attachments a ON a.id=r.attachment_id
        WHERE r.decision_id IN ({marks}) ORDER BY title""", (*ids, *ids))
    result = {}
    for row in rows:
        result.setdefault(row['event_id'], []).append(dict(row))
    return result


def file_link(project_id, file_id, filename):
    return (f'<a href="/project/{project_id}/attachments/{file_id}">{escape(filename)}</a>'
            if file_id else '')


def render_history_notes(project_id, rows, *, expanded=False):
    if not rows:
        return ''
    items = []
    for row in rows:
        status = DECISIONS.get(row['status'])
        note = f'<p class="criterion-note">{escape(row["note"])}</p>' if row['note'] else ''
        link = file_link(project_id, row['file_id'], row['filename'])
        items.append(f'<li><strong>{escape(row["title"])}</strong>{f" · {status}" if status else ""}{note}{link}</li>')
    return ('<details open>' if expanded else '<details>') + '<summary>Критерии и комментарии этой версии</summary><ul>' + ''.join(items) + '</ul></details>'


def criterion_state(row):
    current_evidence = row['reviewed_submission'] == row['submission_id']
    final_decision = row['decision_event'] == 'accept' and row['decision_revision'] == row['stage_revision']
    if row['status'] == 'confirmed' and current_evidence and (row['stage_state'] != 'done' or final_decision):
        return 'confirmed', 'Подтверждено'
    if row['stage_state'] == 'review':
        return 'review', 'На проверке'
    if row['status'] == 'revise':
        return 'revise', 'Нужна доработка'
    return 'open', 'Ожидает подтверждения'


def evidence(project_id, row):
    if criterion_state(row)[0] == 'confirmed' and row['reviewed_file_id']:
        return file_link(project_id, row['reviewed_file_id'], row['reviewed_filename'])
    return file_link(project_id, row['response_file_id'], row['response_filename'])


def feedback(row):
    previous = row['stage_state'] == 'review' and row['status'] and row['reviewed_submission'] != row['submission_id']
    if not row['feedback']:
        return f'<p class="criterion-note">Предыдущая сдача · {DECISIONS[row["status"]]}</p>' if previous else ''
    label = ('К предыдущей сдаче' if previous
             else 'Замечание учителя' if row['status'] == 'revise' else 'Комментарий учителя')
    return f'<p class="criterion-note"><strong>{label}</strong> {escape(row["feedback"])}</p>'


def overview(project_id, groups, goal, goal_label, teacher, accepted):
    rows = [r for group in groups.values() for r in group]
    counts = [criterion_state(r)[0] for r in rows]
    shown = sorted(rows, key=lambda row: (
        {'revise': 0, 'review': 1, 'open': 2, 'confirmed': 3}[criterion_state(row)[0]], row['number'], row['id']))[:5]
    items = []
    for row in shown:
        state, label = criterion_state(row)
        target = f'/project/{project_id}/stages?view_stage={row["number"]}#stage-{row["number"]}-result'
        link = evidence(project_id, row)
        feedback_link = ''
        if row['decision_event'] == 'return' and row['feedback']:
            href = escape(stage_feedback_url(project_id, row['number'], row['decision_id']), quote=True)
            feedback_link = f'<a class="criterion-source" href="{href}">К замечанию</a>'
        items.append(f'''<li class="criterion-overview-item"><div><small>Этап {row["number"]}</small>
            <a href="{target}">{escape(row["title"])}</a></div><span class="criterion-state" data-criterion-state="{state}">{label}</span>
            {feedback(row)}{feedback_link}{f'<small class="criterion-source">{link}</small>' if link else ''}</li>''')
    summary = f'Подтверждено {counts.count("confirmed")} из {len(rows)}' if rows else 'Критерии ещё не заданы'
    goal_html = f'<p class="criterion-goal"><strong>{escape(goal_label)}</strong> {escape(goal)}</p>' if goal else ''
    manage = f'<a href="/project/{project_id}/criteria">Критерии проекта</a>'
    if teacher and not rows:
        manage = f'<a href="/project/{project_id}/criteria#criterion-add">Задать критерии</a>'
    goal_link = f' <a href="/project/{project_id}/preparation#prep-goal">Цель в плане выступления</a>' if not teacher else ''
    return f'''<section class="criterion-overview" aria-labelledby="criterion-overview-title">
        <header><div><h2 id="criterion-overview-title">Что подтверждено</h2><p>{summary}</p></div>
        <a class="btn secondary" href="/project/{project_id}/passport">Паспорт проекта <small>{accepted}/5</small></a></header>
        {goal_html}{('<ul>' + ''.join(items) + '</ul>') if items else ''}
        <footer>{manage}{goal_link}</footer></section>'''


def render_manage(project_id, groups, stages, teacher, token, selected):
    base = f'/project/{project_id}/criteria'
    editable = {s['number'] for s in stages if s['state'] != 'done' and
                (s['state'] != 'review' or s.get('tracked_submission'))}
    blocks = []
    for stage in stages:
        number = stage['number']
        criteria = groups.get(number, [])
        if not criteria:
            continue
        items = []
        for row in criteria:
            state, label = criterion_state(row)
            editor = ''
            if teacher and number in editable and not (row['status'] or row['has_response'] or row['draft_note']):
                cid = row['id']
                editor = f'''<details><summary>Изменить критерий</summary><form method="post" action="{base}">
                    {hidden_token(token)}<input type="hidden" name="number" value="{number}">
                    <input type="hidden" name="criterion_id" value="{cid}">
                    <input type="hidden" name="original_title" value="{escape(row["title"], quote=True)}">
                    <label>Название критерия<input name="title" value="{escape(row["title"], quote=True)}" maxlength="{TITLE_LIMIT}"></label>
                    <div class="criterion-actions"><button name="action" value="rename">Сохранить название</button>
                    <button class="secondary" name="action" value="remove" formnovalidate>Удалить критерий</button></div></form></details>'''
            items.append(f'''<li><div class="criterion-line"><strong>{escape(row["title"])}</strong>
                <span class="criterion-state" data-criterion-state="{state}">{label}</span></div>{feedback(row)}
                {evidence(project_id, row)}{editor}</li>''')
        blocks.append(f'''<section class="criterion-panel"><h2>Этап {number} · {escape(stage["title"])}</h2>
            <ul class="criterion-list">{''.join(items)}</ul>
            <a href="/project/{project_id}/stages?view_stage={number}#stage-{number}-result">Результат этапа</a></section>''')
    available = [s for s in stages if s['number'] in editable and len(groups.get(s['number'], [])) < MAX_CRITERIA]
    creation = ''
    if teacher and available:
        options = ''.join(f'<option value="{s["number"]}" {"selected" if s["number"] == selected else ""}>'
                          f'{s["number"]} · {escape(s["title"])}</option>' for s in available)
        creation = f'''<section id="criterion-add" class="criterion-panel"><h2>Добавить критерий</h2>
            <form method="post" action="{base}">{hidden_token(token)}<label>Этап<select name="number">{options}</select></label>
            <label>Что должен подтвердить результат<input name="title" maxlength="{TITLE_LIMIT}" required
            placeholder="Например, есть замеры из трёх кабинетов"></label>
            <button name="action" value="add">Добавить критерий</button></form></section>'''
    return STYLE + creation + (''.join(blocks) if blocks else '<p>Критерии проекта появятся, когда их добавит учитель.</p>')


def student_fields(criteria, values=None, upload_names=()):
    if not criteria:
        return ''
    items = []
    for row in criteria:
        cid = row['id']
        note = values.get(f'evidence_note_{cid}', row['draft_note']) if values is not None else row['draft_note']
        position = values.get(f'evidence_file_{cid}', '') if values is not None else ''
        filename = next((name for i, name in enumerate(upload_names, 1) if str(i) == position), '')
        reminder = f'<p>Выберите заново для критерия: <strong>{escape(filename)}</strong></p>' if filename else ''
        options = ''.join(f'<option value="{i}">Файл {i}</option>' for i in range(1, FILE_COUNT + 1))
        items.append(f'''<fieldset class="criterion-fields"><legend>{escape(row["title"])}</legend>{feedback(row)}{reminder}
            <label>Пояснение<textarea name="evidence_note_{cid}" rows="2" maxlength="{NOTE_LIMIT}">{escape(note)}</textarea></label>
            <label>Файл для критерия<select name="evidence_file_{cid}" data-criterion-upload>
                <option value="">Выбрать из вложений</option>{options}</select></label></fieldset>''')
    return ('<div class="criterion-form"><h4>Подтверждения по критериям</h4>'
            '<p>Добавьте пояснение или выберите файл из вложений. Файл привяжется при отправке.</p>'
            + version_field(criteria) + ''.join(items) + '</div>')


def current_summary(project_id, criteria):
    if not criteria:
        return ''
    items = []
    for row in criteria:
        state, label = criterion_state(row)
        response = f'<p class="criterion-note">{escape(row["response"])}</p>' if row['response'] else ''
        items.append(f'''<li><div class="criterion-line"><strong>{escape(row["title"])}</strong>
            <span class="criterion-state" data-criterion-state="{state}">{label}</span></div>
            {feedback(row)}{response}{evidence(project_id, row)}</li>''')
    return f'<section class="criterion-stage"><h4>Критерии этапа</h4><ul class="criterion-list">{"".join(items)}</ul></section>'


def review_fields(project_id, criteria, files, values=None, report=None):
    if not criteria:
        return ''
    values = values or {}
    file_ids = {f['id'] for f in files}
    suggestions = {c['criterion'].casefold(): c for c in (report or {}).get('checks', ())
                   if c['quote'] and c.get('attachment') in file_ids}
    items = []
    for row in criteria:
        cid = row['id']
        status = values.get(f'criterion_status_{cid}', '')
        note = values.get(f'criterion_note_{cid}', '')
        selected = values.get(f'criterion_file_{cid}', str(row['response_file_id'] or ''))
        if not row['submission_id'] or row['submission_revision'] != row['stage_revision']:
            continue
        choices = ''.join(f'<option value="{key}" {"selected" if key == status else ""}>{label}</option>'
                          for key, label in DECISIONS.items())
        sources = ''.join(f'<option value="{f["id"]}" {"selected" if str(f["id"]) == selected else ""}>'
                          f'{escape(f["filename"])}</option>' for f in files)
        response = f'<p class="criterion-note"><strong>Ответ ученика</strong> {escape(row["response"])}</p>' if row['response'] else ''
        previous_file = file_link(project_id, row['previous_file_id'], row['previous_filename'])
        previous_text = escape(row['previous_response'] or '')
        changes = []
        if row['previous_submission_id']:
            if (row['response'] or '') != (row['previous_response'] or ''):
                changes.append('Добавлено пояснение' if not row['previous_response'] else
                               'Пояснение изменено' if row['response'] else 'Пояснение убрано')
            if (row['response_hash'], row['response_filename']) != (row['previous_hash'], row['previous_filename']):
                changes.append('Добавлен файл' if not row['previous_file_id'] else
                               'Файл изменён' if row['response_file_id'] else 'Файл убран')
        comparison = (f'<details><summary>Что было в предыдущей сдаче</summary><p class="criterion-note">{previous_text}</p>{previous_file}</details>'
                      if previous_text or previous_file else '')
        if changes:
            comparison = f'<p class="criterion-change">{" · ".join(changes)}</p>' + comparison
        suggestion = suggestions.get(row['title'].casefold())
        ai = (f'<details><summary>Цитата из ИИ-разбора</summary><blockquote>{escape(suggestion["quote"])}</blockquote>'
              f'{file_link(project_id, suggestion["attachment"], suggestion["label"])}</details>' if suggestion else '')
        select_file = (f'<label>Подтверждающий файл<select name="criterion_file_{cid}">'
                       f'<option value="">Файл ученика, если выбран</option>{sources}</select></label>' if files else '')
        items.append(f'''<fieldset class="criterion-fields"><legend>{escape(row["title"])}</legend>
            {feedback(row)}{response}{evidence(project_id, row)}{comparison}{ai}
            <label>Решение по критерию<select name="criterion_status_{cid}">
                <option value="">Выбрать решение</option>{choices}</select></label>
            <label>Комментарий к критерию<textarea name="criterion_note_{cid}" rows="2" maxlength="{NOTE_LIMIT}">{escape(note)}</textarea></label>
            {select_file}</fieldset>''')
    return ('<div class="criterion-form"><h4>Критерии этапа</h4>'
            '<p>Подтвердите каждый критерий перед принятием. Для доработки укажите, что изменить.</p>'
            + version_field(criteria) + ''.join(items) + '</div>') if items else ''
