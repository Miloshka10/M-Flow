"""Пять последовательных этапов проекта, отдельно от задач и итоговых баллов."""
from interface import icon

from html import escape
from material_links import safe_web_url
from project_rules import has_project_teacher, TEACHER_REQUIRED
from database import execute_schema, begin_project_write
from notifications import notify_project
from teacher_supervision import participant
from deadlines import format_timestamp
from work_history import before_change, record_change

STAGES = (
    (1, "Выбор темы и согласование с учителем"),
    (2, "Работа над проектом — 1"),
    (3, "Работа над проектом — 2"),
    (4, "Работа над проектом — 3"),
    (5, "Подготовка документации: презентация и описание проекта"),
)
STATE_LABELS = {"todo": "Не начат", "progress": "В работе", "review": "На проверке", "done": "Принят учителем"}
STAGE_DESCRIPTIONS = {
    1: "Сформулируйте тему, цель и ожидаемый результат. Согласуйте их с учителем.",
    2: "Опишите первый результат работы и выбранные способы решения задачи.",
    3: "Покажите развитие проекта: что сделано, проверено и что осталось улучшить.",
    4: "Подготовьте результат работы и объясните, как он решает задачу проекта.",
    5: "Подготовьте описание проекта и доступную учителю ссылку на презентацию.",
}


def stage_needs_revision(stage):
    # Return is stored as progress; no schema migration or synthetic history.
    return bool(stage['state'] == 'progress' and stage.get('reviewed_by'))


def stage_label(stage, current_number=None):
    if current_number is not None and stage['number'] > current_number:
        return 'Заблокирован'
    return 'Нужна доработка' if stage_needs_revision(stage) else STATE_LABELS[stage['state']]


def initialize_stages(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS project_stages (
        project_id INTEGER NOT NULL REFERENCES projects(id),
        number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 5),
        state TEXT NOT NULL CHECK(state IN ('progress', 'review', 'done')),
        result TEXT NOT NULL DEFAULT '',
        presentation_url TEXT NOT NULL DEFAULT '',
        teacher_comment TEXT NOT NULL DEFAULT '',
        submitted_by INTEGER REFERENCES users(id),
        reviewed_by INTEGER REFERENCES users(id),
        revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
        PRIMARY KEY(project_id, number)
    )""")


def get_stages(conn, project_id):
    return stages_from_rows(conn.execute("""
        SELECT s.*, author.username AS author_name, reviewer.username AS reviewer_name
        FROM project_stages s LEFT JOIN users author ON author.id=s.submitted_by
        LEFT JOIN users reviewer ON reviewer.id=s.reviewed_by
        WHERE s.project_id=? ORDER BY s.number""", (project_id,)))


def stages_from_rows(rows):
    """Complete the five stages from either single-project or batched rows."""
    stored = {row["number"]: dict(row) for row in rows}
    stages = []
    for number, title in STAGES:
        stage = dict(number=number, title=title, state="todo", result="", presentation_url="",
                     teacher_comment="", revision=0, author_name=None, reviewer_name=None, updated_at="")
        stage.update(stored.get(number, {}))
        stages.append(stage)
    return stages


def stage_summary(stages):
    completed = sum(stage["state"] == "done" for stage in stages)
    current = next((stage for stage in stages if stage["state"] != "done"), None)
    return completed, current


def change_stage(conn, project_id, user, form):
    """Caller checks project membership and CSRF. Transaction prevents lost updates."""
    begin_project_write(conn, project_id)
    try:
        if not participant(conn, project_id, user['id']):
            raise ValueError('Доступ к проекту изменился. Откройте главную страницу; сохранение недоступно.')
        stages = get_stages(conn, project_id)
        _, current = stage_summary(stages)
        if current is None:
            raise ValueError("Все пять этапов уже приняты учителем.")
        if form.get("number") != str(current["number"]):
            raise ValueError("Этапы проходят по порядку. Обновите страницу и откройте текущий этап.")
        if form.get("revision") != str(current["revision"]):
            raise ValueError("Этап уже изменён другим участником. Обновите страницу перед сохранением.")
        action = form.get("action")
        result, url, comment = current["result"], current["presentation_url"], current["teacher_comment"]
        submitted_by = current.get("submitted_by")
        reviewed_by = current.get("reviewed_by")
        if user["role"] == "student":
            if action not in ("save", "submit") or current["state"] not in ("todo", "progress"):
                raise ValueError("Ученик может сохранять и отправлять только текущий этап в работе.")
            result = form.get("result", "").strip()
            url = form.get("presentation_url", "").strip() if current["number"] == 5 else ""
            if len(result) > 5000 or len(url) > 2000:
                raise ValueError("Описание — до 5000 символов, ссылка — до 2000 символов.")
            if url:
                if not safe_web_url(url):
                    raise ValueError("Укажите полную ссылку на презентацию с https:// или http://, без логина и пароля в адресе.")
            if action == "submit" and (not result or (current["number"] == 5 and not url)):
                raise ValueError("Для проверки заполните результат этапа; на пятом этапе нужны описание и ссылка на презентацию.")
            if action == "submit" and not has_project_teacher(conn, project_id):
                raise ValueError(TEACHER_REQUIRED)
            state = "review" if action == "submit" else "progress"
            submitted_by = user["id"]
        elif user["role"] == "teacher":
            if action not in ("accept", "return") or current["state"] != "review":
                raise ValueError("Учитель может принять или вернуть только этап, отправленный на проверку.")
            comment = form.get("teacher_comment", "").strip()
            if len(comment) > 2000 or (action == "return" and not comment):
                raise ValueError("При возврате укажите, что исправить. Комментарий — до 2000 символов.")
            reviewed_by = user["id"]
            state = "done" if action == "accept" else "progress"
        else:
            raise ValueError("Недопустимая роль участника.")
        before_change(conn, project_id, 'stage', current['number'], current)
        conn.execute("""INSERT INTO project_stages
            (project_id, number, state, result, presentation_url, teacher_comment, submitted_by, reviewed_by, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, number) DO UPDATE SET
            state=excluded.state, result=excluded.result, presentation_url=excluded.presentation_url,
            teacher_comment=excluded.teacher_comment, submitted_by=excluded.submitted_by,
            reviewed_by=excluded.reviewed_by, revision=excluded.revision, updated_at=CAST(CURRENT_TIMESTAMP AS TEXT)""",
            (project_id, current["number"], state, result, url, comment, submitted_by, reviewed_by, current["revision"] + 1))
        record_change(conn, project_id, 'stage', current['number'], action,
                      dict(current, state=state, result=result, presentation_url=url,
                           teacher_comment=comment, submitted_by=submitted_by, reviewed_by=reviewed_by), user)
        if action == 'submit':
            notify_project(conn, project_id, 'teacher',
                f'stage:{project_id}:{current["number"]}:{current["revision"] + 1}',
                f'Этап {current["number"]} отправлен на проверку', '', f'/project/{project_id}/stages#stage-{current["number"]}')
        elif action in ('accept', 'return'):
            notify_project(conn, project_id, 'student',
                f'stage:{project_id}:{current["number"]}:{current["revision"] + 1}',
                f'Этап {current["number"]}: ' + ('принят' if action == 'accept' else 'нужна доработка'),
                comment, f'/project/{project_id}/stages#stage-{current["number"]}')
        conn.commit()
        return {"save": "Результат этапа сохранён.", "submit": "Этап отправлен учителю на проверку.",
                "accept": "Этап принят. Следующий этап открыт.", "return": "Этап возвращён на доработку."}[action] if not (action == "accept" and current["number"] == 5) else "Все пять этапов проекта приняты!"
    except Exception:
        conn.rollback()
        raise


def render_stages(stages, user, token, submitted=None, has_teacher=True, project_id=None,
                  only_number=None, compact=False, return_to=None):
    completed, current = stage_summary(stages)
    current_number = current["number"] if current else None
    html = '' if compact else f'<p class="stage-summary">Принято {completed} из 5 этапов · {completed * 20}%</p><div class="stage-timeline">'
    if user['role'] == 'student' and not has_teacher:
        html += f'<p role="alert">{TEACHER_REQUIRED}</p>'
    for stage in stages:
        number = stage["number"]
        if only_number is not None and number != only_number:
            continue
        locked = current_number is not None and number > current_number
        label = stage_label(stage, current_number)
        explanation = f'Откроется после принятия этапа {number - 1} учителем.' if locked else STAGE_DESCRIPTIONS[number]
        html += (f'<section class="pw-result-card"><h3>{"Описание проекта" if number == 5 else "Результат этапа"}</h3><p class="pw-state">{label}</p>' if compact else
                 f'<section id="stage-{number}" class="stage-card {stage["state"]} {"locked" if locked else ""}"><div class="stage-heading"><span class="stage-number">{number}</span><h2>{escape(stage["title"])}</h2></div><p>{label}</p><p>{explanation}</p>')
        if locked and compact:
            html += f'<p>{explanation}</p>'
        if stage["result"]:
            html += ('' if compact else f'<h3>{"Описание проекта" if number == 5 else "Результат этапа"}</h3>') + f'<p class="stage-text">{escape(stage["result"])}</p>'
        if safe_web_url(stage["presentation_url"]):
            html += f'<p><a class="action-link" href="{escape(stage["presentation_url"], quote=True)}" target="_blank" rel="noopener noreferrer">Открыть презентацию {icon('external')}</a></p>'
        elif stage['presentation_url']:
            html += '<p>Формат сохранённой ссылки на презентацию не поддерживается.</p>'
        if stage.get("author_name"):
            html += f'<small>Автор результата: {escape(stage["author_name"])} · Обновлено: {format_timestamp(stage["updated_at"])}</small>'
        if stage.get("reviewer_name"):
            html += f'<p>{"Учитель предыдущей сдачи" if stage["state"] == "review" else "Учитель"}: {escape(stage["reviewer_name"])}</p>'
        if stage["teacher_comment"]:
            heading = 'Замечание к предыдущей сдаче. Новая версия ещё не проверена.' if stage['state'] == 'review' else 'Комментарий учителя:'
            html += f'<p class="stage-text stage-comment">{heading} {escape(stage["teacher_comment"])}</p>'
        elif stage['state'] == 'done' or stage_needs_revision(stage):
            html += '<p>Комментарий не указан. При необходимости уточните решение у учителя проекта.</p>'
        if stage['state'] == 'done':
            html += f'<p>Этап {number} принят учителем. {"Все пять этапов приняты." if number == 5 else "Можно продолжить маршрут проекта."} <a href="/project/{stage["project_id"]}">Посмотреть следующий шаг {icon('arrow')}</a></p>'
        if number == current_number:
            values = submitted or stage
            pid = stage.get('project_id', project_id)
            prefix = f'<form method="post" action="/project/{pid}/stages" data-guard-draft data-draft-unsaved="{int(submitted is not None)}"><input type="hidden" name="csrf_token" value="{token}"><input type="hidden" name="number" value="{number}"><input type="hidden" name="revision" value="{stage["revision"]}">'
            if return_to:
                prefix += f'<input type="hidden" name="return_to" value="{escape(return_to, quote=True)}">'
            if user["role"] == "student" and stage["state"] in ("todo", "progress"):
                html += '<p>Исправьте результат по замечанию и отправьте его снова. Сохранение черновика не отправляет работу учителю.</p>' if stage_needs_revision(stage) else '<p>Ещё не отправлено. Сохраните черновик или отправьте готовый результат учителю.</p>'
                if compact:
                    html += f'<details class="pw-editor" {"open" if submitted is not None else ""}><summary data-ui-icon="edit">{"Доработать результат" if stage_needs_revision(stage) else "Редактировать черновик" if stage["result"] else "Добавить результат"}</summary><div class="pw-editor-content"><h2>Результат этапа {number}</h2>'
                    if stage_needs_revision(stage) and stage['teacher_comment']:
                        html += f'<p class="stage-comment">Что исправить: {escape(stage["teacher_comment"])}</p>'
                result_label = "Выбранная тема" if number == 1 else "Описание проекта" if number == 5 else "Что сделано на этом этапе"
                html += prefix + f'<label>{result_label}<textarea name="result" rows="5" maxlength="5000">{escape(values.get("result", ""))}</textarea></label>'
                if number == 5:
                    html += f'<label>Ссылка на презентацию<input type="url" name="presentation_url" maxlength="2000" placeholder="https://…" value="{escape(values.get("presentation_url", ""), quote=True)}"></label><p>Проверьте, что учитель сможет открыть презентацию по ссылке.</p>'
                html += f'<div class="stage-actions"><button name="action" value="save" data-ui-icon="save">Сохранить черновик</button><button name="action" value="submit" {"disabled" if not has_teacher else ""}>Отправить учителю</button></div></form>'
                if compact:
                    html += '</div></details>'
            elif user["role"] == "teacher" and stage["state"] == "review":
                comment = submitted.get('teacher_comment', '') if submitted is not None else ''
                html += prefix + f'<label>Комментарий учителя<textarea name="teacher_comment" rows="3" maxlength="2000">{escape(comment)}</textarea></label><div class="stage-actions"><button name="action" value="accept">{"Согласовать тему" if number == 1 else "Принять этап"}</button><button name="action" value="return" data-ui-icon="return">Вернуть на доработку</button></div></form>'
            else:
                html += f'<p>{"Ожидается проверка учителя. Пока результат менять нельзя." if stage["state"] == "review" else "Ожидается результат от учеников проекта."}</p>'
        pid = stage.get('project_id', project_id)
        if pid is not None:
            html += f'<p><a class="action-link" href="/project/{pid}/history?work=stage-{number}">История отправок и решений {icon('arrow')}</a></p>'
        html += '</section>'
    return html + ('' if compact else '</div>')
