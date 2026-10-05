"""Пять последовательных этапов проекта, отдельно от задач и итоговых баллов."""

from html import escape
from urllib.parse import urlsplit

STAGES = (
    (1, "Выбор темы и согласование с учителем"),
    (2, "Работа над проектом — 1"),
    (3, "Работа над проектом — 2"),
    (4, "Работа над проектом — 3"),
    (5, "Подготовка документации: презентация и описание проекта"),
)
STATE_LABELS = {"todo": "Не начат", "progress": "В работе", "review": "На проверке", "done": "Принят учителем"}


def initialize_stages(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS project_stages (
        project_id INTEGER NOT NULL REFERENCES projects(id),
        number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 5),
        state TEXT NOT NULL CHECK(state IN ('progress', 'review', 'done')),
        result TEXT NOT NULL DEFAULT '',
        presentation_url TEXT NOT NULL DEFAULT '',
        teacher_comment TEXT NOT NULL DEFAULT '',
        submitted_by INTEGER REFERENCES users(id),
        reviewed_by INTEGER REFERENCES users(id),
        revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id, number)
    )""")


def get_stages(conn, project_id):
    stored = {row["number"]: dict(row) for row in conn.execute("""
        SELECT s.*, author.username AS author_name, reviewer.username AS reviewer_name
        FROM project_stages s LEFT JOIN users author ON author.id=s.submitted_by
        LEFT JOIN users reviewer ON reviewer.id=s.reviewed_by
        WHERE s.project_id=? ORDER BY s.number""", (project_id,))}
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
    conn.execute("BEGIN IMMEDIATE")
    try:
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
                try:
                    parsed = urlsplit(url)
                    valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname) and not any(c.isspace() for c in url)
                except ValueError:
                    valid_url = False
                if not valid_url:
                    raise ValueError("Укажите полную ссылку на презентацию, начинающуюся с https:// или http://.")
            if action == "submit" and (not result or (current["number"] == 5 and not url)):
                raise ValueError("Для проверки заполните результат этапа; на пятом этапе нужны описание и ссылка на презентацию.")
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
        conn.execute("""INSERT INTO project_stages
            (project_id, number, state, result, presentation_url, teacher_comment, submitted_by, reviewed_by, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, number) DO UPDATE SET
            state=excluded.state, result=excluded.result, presentation_url=excluded.presentation_url,
            teacher_comment=excluded.teacher_comment, submitted_by=excluded.submitted_by,
            reviewed_by=excluded.reviewed_by, revision=excluded.revision, updated_at=CURRENT_TIMESTAMP""",
            (project_id, current["number"], state, result, url, comment, submitted_by, reviewed_by, current["revision"] + 1))
        conn.commit()
        return {"save": "Результат этапа сохранён.", "submit": "Этап отправлен учителю на проверку.",
                "accept": "Этап принят. Следующий этап открыт.", "return": "Этап возвращён на доработку."}[action] if not (action == "accept" and current["number"] == 5) else "Все пять этапов проекта приняты!"
    except Exception:
        conn.rollback()
        raise


def render_stages(stages, user, token, submitted=None):
    completed, current = stage_summary(stages)
    current_number = current["number"] if current else None
    html = f'<p class="stage-summary">Принято {completed} из 5 этапов · {completed * 20}%</p><div class="stage-timeline">'
    for stage in stages:
        number = stage["number"]
        locked = current_number is not None and number > current_number
        label = "Откроется после предыдущего этапа" if locked else STATE_LABELS[stage["state"]]
        html += f'<section class="stage-card {stage["state"]} {"locked" if locked else ""}"><div class="stage-heading"><span class="stage-number">{number}</span><h2>{escape(stage["title"])}</h2></div><p>{label}</p>'
        if stage["result"]:
            html += f'<h3>{"Описание проекта" if number == 5 else "Результат этапа"}</h3><p class="stage-text">{escape(stage["result"])}</p>'
        if stage["presentation_url"]:
            html += f'<p><a href="{escape(stage["presentation_url"], quote=True)}" target="_blank" rel="noopener noreferrer">Открыть презентацию ↗</a></p>'
        if stage.get("author_name"):
            html += f'<small>Автор результата: {escape(stage["author_name"])} · Обновлено: {escape(stage["updated_at"])}</small>'
        if stage.get("reviewer_name"):
            html += f'<p>Учитель: {escape(stage["reviewer_name"])}</p>'
        if stage["teacher_comment"]:
            html += f'<p class="stage-text stage-comment">Комментарий учителя: {escape(stage["teacher_comment"])}</p>'
        if number == current_number:
            values = submitted or stage
            prefix = f'<form method="post"><input type="hidden" name="csrf_token" value="{token}"><input type="hidden" name="number" value="{number}"><input type="hidden" name="revision" value="{stage["revision"]}">'
            if user["role"] == "student" and stage["state"] in ("todo", "progress"):
                result_label = "Выбранная тема" if number == 1 else "Описание проекта" if number == 5 else "Что сделано на этом этапе"
                html += prefix + f'<label>{result_label}<textarea name="result" rows="5" maxlength="5000">{escape(values.get("result", ""))}</textarea></label>'
                if number == 5:
                    html += f'<label>Ссылка на презентацию<input type="url" name="presentation_url" maxlength="2000" placeholder="https://…" value="{escape(values.get("presentation_url", ""), quote=True)}"></label><p>Проверьте, что учитель сможет открыть презентацию по ссылке.</p>'
                html += '<div class="stage-actions"><button name="action" value="save">Сохранить черновик</button><button name="action" value="submit">Отправить учителю</button></div></form>'
            elif user["role"] == "teacher" and stage["state"] == "review":
                html += prefix + f'<label>Комментарий учителя<textarea name="teacher_comment" rows="3" maxlength="2000">{escape(values.get("teacher_comment", ""))}</textarea></label><div class="stage-actions"><button name="action" value="accept">{"Согласовать тему" if number == 1 else "Принять этап"}</button><button name="action" value="return">Вернуть на доработку</button></div></form>'
            else:
                html += f'<p>{"Ожидается проверка учителя. Пока результат менять нельзя." if stage["state"] == "review" else "Ожидается результат от учеников проекта."}</p>'
        html += '</section>'
    return html + '</div>'
