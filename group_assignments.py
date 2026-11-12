"""One published assignment per learning group."""

from html import escape
from urllib.parse import urlsplit

from database import execute_schema_script
from deadlines import parse_deadline
from interface import icon
from material_links import safe_web_url
from teamwork import hidden_token


TITLE_LIMIT = 120
TEXT_LIMIT = 1200
LINK_COUNT = 3
LINK_TEXT_LIMIT = LINK_COUNT * 2002
FIELD_LIMITS = {
    'title': TITLE_LIMIT, 'goal': TEXT_LIMIT, 'criteria': TEXT_LIMIT,
    'due_date': 10, 'materials': LINK_TEXT_LIMIT,
}


def initialize_group_assignments(conn):
    execute_schema_script(conn, """
        CREATE TABLE IF NOT EXISTS learning_group_assignments (
            group_id INTEGER PRIMARY KEY NOT NULL REFERENCES learning_groups(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            criteria TEXT NOT NULL,
            due_date TEXT NOT NULL,
            materials TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL CHECK(revision >= 1)
        );
    """)


def assignment_for(conn, group_id):
    return conn.execute('SELECT * FROM learning_group_assignments WHERE group_id=?', (group_id,)).fetchone()


def save_assignment(conn, group_id, form):
    """The caller holds the group write lock and has checked its teacher and state."""
    existing = assignment_for(conn, group_id)
    revision = existing['revision'] if existing else 0
    if form.get('assignment_revision') != str(revision):
        raise ValueError('Задание уже изменилось. Ниже открыта актуальная версия.')

    title = form.get('title', '').strip()
    goal = form.get('goal', '').strip()
    criteria = form.get('criteria', '').strip()
    due = parse_deadline(form.get('due_date', ''))
    materials = form.get('materials', '').strip()
    if any('\0' in value for value in (title, goal, criteria)):
        raise ValueError('Удалите недопустимые символы из текста задания.')
    links = list(dict.fromkeys(line.strip() for line in materials.splitlines() if line.strip())) if len(materials) <= LINK_TEXT_LIMIT else []
    if not title or len(title) > TITLE_LIMIT:
        raise ValueError(f'Укажите название задания до {TITLE_LIMIT} символов.')
    if not goal or len(goal) > TEXT_LIMIT or not criteria or len(criteria) > TEXT_LIMIT:
        raise ValueError(f'Укажите цель и критерии. Каждое поле должно быть не длиннее {TEXT_LIMIT} символов.')
    if due is None:
        raise ValueError('Укажите срок сдачи задания.')
    if (len(materials) > LINK_TEXT_LIMIT or len(links) > LINK_COUNT
            or any(not safe_web_url(link) or '\\' in link for link in links)):
        raise ValueError(f'Добавьте до {LINK_COUNT} ссылок, начинающихся с https:// или http://, по одной на строку.')

    values = (title, goal, criteria, due.isoformat(), '\n'.join(links))
    if existing:
        conn.execute('''UPDATE learning_group_assignments SET title=?,goal=?,criteria=?,due_date=?,materials=?,
            revision=revision+1 WHERE group_id=?''', (*values, group_id))
    else:
        conn.execute('''INSERT INTO learning_group_assignments(group_id,title,goal,criteria,due_date,materials,revision)
            VALUES(?,?,?,?,?,?,1)''', (group_id, *values))


def _assignment_editor(assignment, token, submitted):
    revision = assignment['revision'] if assignment else 0
    values = dict(assignment) if assignment else {}
    if submitted is not None and submitted.get('assignment_revision') == str(revision):
        values = {key: submitted.get(key, '')[:limit] for key, limit in FIELD_LIMITS.items()}
    fields = {key: escape(values.get(key, ''), quote=True) for key in FIELD_LIMITS}
    title = 'Изменить задание' if assignment else 'Добавить задание'
    action = 'Сохранить изменения' if assignment else 'Опубликовать задание'
    return f'''<details class="ds-guide group-assignment-editor"{' open' if submitted is not None else ''}>
        <summary>{title}</summary>
        <form method="post" class="group-form">{hidden_token(token)}
            <input type="hidden" name="action" value="save_assignment">
            <input type="hidden" name="assignment_revision" value="{revision}">
            <label>Название задания<input name="title" maxlength="{TITLE_LIMIT}" value="{fields['title']}" required></label>
            <label>Срок сдачи<input type="date" name="due_date" value="{fields['due_date']}" required></label>
            <label>Цель<textarea name="goal" maxlength="{TEXT_LIMIT}" rows="3" required>{fields['goal']}</textarea></label>
            <label>Критерии проверки<textarea name="criteria" maxlength="{TEXT_LIMIT}" rows="3" required>{fields['criteria']}</textarea></label>
            <label>Ссылки на материалы <small>До {LINK_COUNT} ссылок, по одной на строку</small>
                <textarea name="materials" maxlength="{LINK_TEXT_LIMIT}" rows="2">{fields['materials']}</textarea></label>
            <button data-ui-icon="{'save' if assignment else 'plus'}">{action}</button>
        </form>
    </details>'''


def render_assignment(assignment, token, *, teacher, archived, submitted=None):
    editor = _assignment_editor(assignment, token, submitted) if teacher and not archived else ''
    if not assignment:
        if not editor:
            return ''
        return f'<section class="group-panel group-assignment"><h2>Задание для группы</h2>{editor}</section>'

    due = parse_deadline(assignment['due_date'])
    due_html = f'<time datetime="{due.isoformat()}">{due.day:02d}.{due.month:02d}.{due.year:04d}</time>' if due else 'Уточните у учителя'
    material_items = []
    for link in assignment['materials'].splitlines():
        if safe_web_url(link) and '\\' not in link:
            host = escape(urlsplit(link).hostname)
            material_items.append(f'<li><a href="{escape(link, quote=True)}" rel="noreferrer">Материал {len(material_items) + 1} · {host}</a></li>')
    materials = '<h3>Материалы</h3><ul class="group-materials">' + ''.join(material_items) + '</ul>' if material_items else ''
    project_link = ''
    if not teacher and not archived:
        project_link = f'<a class="action-link group-assignment-project" href="#group-project">К проекту {icon("arrow")}</a>'
    return f'''<section class="group-panel group-assignment" aria-labelledby="group-assignment-title">
        <div class="group-assignment-head">
            <div><span class="eyebrow">ЗАДАНИЕ ГРУППЫ</span>
                <h2 id="group-assignment-title">{escape(assignment['title'])}</h2></div>
            <p class="group-assignment-due"><span>Срок сдачи</span>{due_html}</p>
        </div>
        <p class="group-text group-assignment-goal">{escape(assignment['goal'])}</p>
        <details class="ds-guide"><summary>{'Критерии и материалы' if materials else 'Критерии проверки'}</summary>
            <p class="group-text">{escape(assignment['criteria'])}</p>{materials}</details>
        {project_link}{editor}
    </section>'''
