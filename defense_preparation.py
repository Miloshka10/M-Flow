"""Shared speaking notes and a read-only view of existing project materials."""
from html import escape
from material_links import safe_web_url

from database import execute_schema, begin_project_write
from deadlines import format_timestamp
from interface import icon
from project_stages import stage_label
from teacher_supervision import participant
from teamwork import hidden_token
from video_defense import DEFENSE_STATES


POINTS = (
    ('problem', 'Проблема', 'Какую проблему вы заметили и кому важно её решить?',
     'Начните с конкретной ситуации или наблюдения.'),
    ('goal', 'Цель', 'Что вы хотели получить в результате?',
     'Сформулируйте цель и объясните, как поймёте, что достигли её.'),
    ('process', 'Ход работы', 'Как работала команда и что сделал каждый участник?',
     'Выделите главные шаги, вклад участников и одно важное решение команды.'),
    ('outcome', 'Результат', 'Что получилось и как вы это проверили?',
     'Приведите пример, измерение или демонстрацию результата.'),
    ('conclusion', 'Выводы', 'Чему вы научились и что улучшили бы дальше?',
     'Назовите вывод, ограничение работы и возможный следующий шаг.'),
)
MAX_POINT = 1200


def initialize_preparation(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS project_defense_plans (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id),
        problem TEXT NOT NULL DEFAULT '',
        goal TEXT NOT NULL DEFAULT '',
        process TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT '',
        conclusion TEXT NOT NULL DEFAULT '',
        updated_by INTEGER REFERENCES users(id),
        revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT))
    )""")


def plan_for(conn, project_id):
    row = conn.execute('''SELECT p.*,u.username AS author_name FROM project_defense_plans p
        LEFT JOIN users u ON u.id=p.updated_by WHERE p.project_id=?''', (project_id,)).fetchone()
    if row:
        return dict(row)
    return dict(project_id=project_id, revision=0, updated_at='', author_name=None,
                **{name: '' for name, *_ in POINTS})


def save_plan(conn, project_id, user, form):
    begin_project_write(conn, project_id)
    try:
        if user['role'] != 'student' or not participant(conn, project_id, user['id']):
            raise ValueError('Сохранять план могут только ученики — участники проекта.')
        current = plan_for(conn, project_id)
        if form.get('revision') != str(current['revision']):
            raise ValueError('План уже изменён другим участником. Ваши тезисы сохранены на экране; сравните их с актуальным планом.')
        if form.get('action') != 'save':
            raise ValueError('Выберите сохранение плана.')
        values = [form.get(name, '').strip() for name, *_ in POINTS]
        if any(len(value) > MAX_POINT for value in values):
            raise ValueError(f'Каждый тезис — не длиннее {MAX_POINT} символов.')
        conn.execute('''INSERT INTO project_defense_plans
            (project_id,problem,goal,process,outcome,conclusion,updated_by,revision)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
            problem=excluded.problem,goal=excluded.goal,process=excluded.process,
            outcome=excluded.outcome,conclusion=excluded.conclusion,updated_by=excluded.updated_by,
            revision=excluded.revision,updated_at=CAST(CURRENT_TIMESTAMP AS TEXT)''',
            (project_id, *values, user['id'], current['revision'] + 1))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def material_url(value):
    """Never fetch external URLs; reject unsafe legacy links in this view too."""
    return value if safe_web_url(value) else ''


def render_preparation(project_id, user, plan, stages, defense, token, form=None):
    base = f'/project/{project_id}'
    student = user['role'] == 'student'
    values = form if form is not None else plan
    stale = form is not None and form.get('revision') != str(plan['revision'])
    filled = sum(bool(plan[name].strip()) for name, *_ in POINTS)
    metadata = (f'Сохранено: {format_timestamp(plan["updated_at"])} · {escape(plan["author_name"] or "участник проекта")}'
                if plan['revision'] else 'План пока не сохранён')
    fields = ''
    for number, (name, title, prompt, hint) in enumerate(POINTS, 1):
        content = (f'<label for="prep-{name}">{escape(prompt)}</label>'
                   f'<p class="prep-hint" id="prep-{name}-hint">{escape(hint)} До {MAX_POINT} символов.</p>'
                   f'<textarea id="prep-{name}" name="{name}" rows="3" maxlength="{MAX_POINT}" '
                   f'aria-describedby="prep-{name}-hint">{escape(values.get(name, ""))}</textarea>'
                   if student else
                   f'<p class="prep-note{" empty" if not plan[name].strip() else ""}">{escape(plan[name]) if plan[name].strip() else "Тезис пока не добавлен."}</p>')
        fields += (f'<section class="prep-point" aria-labelledby="prep-{name}-title">'
                   f'<div class="prep-point-heading"><span aria-hidden="true">{number:02}</span>'
                   f'<h3 id="prep-{name}-title">{title}</h3></div>{content}</section>')
    if student:
        conflict = ''
        if stale:
            current = ''.join(f'<h4>{title}</h4><p class="prep-note">{escape(plan[name]) or "Не заполнено"}</p>'
                              for name, title, *_ in POINTS)
            conflict = (f'<details class="prep-conflict"><summary>Актуальный сохранённый план</summary>{current}</details>'
                        f'<p><a class="action-link" href="{base}/preparation" target="_blank" rel="noopener noreferrer">'
                        f'Открыть актуальный план в новой вкладке {icon("external")}</a></p>')
        fields = (f'<form method="post" class="prep-form" data-guard-draft data-draft-unsaved="{int(form is not None)}">{hidden_token(token)}'
                  f'<input type="hidden" name="revision" value="{escape(str(values.get("revision", plan["revision"])), quote=True)}">'
                  + fields + f'<div class="prep-save"><button name="action" value="save" data-ui-icon="save"'
                  + (' disabled' if stale else '') + '>Сохранить план</button>'
                  '<p>Общий план команды. Сохраните тезисы перед уходом со страницы.</p></div></form>' + conflict)

    final = stages[4]
    presentation = material_url(final['presentation_url'])
    video = material_url(defense['video_url'])
    description = final['result'].strip()
    materials = (
        ('Описание проекта', bool(description), 'Из результата пятого этапа',
         f'{base}/stages#stage-5-result', '', description[:160] + ('…' if len(description) > 160 else '')),
        ('Презентация', bool(presentation), 'Ссылка из пятого этапа',
         f'{base}/stages#stage-5-result', presentation, ''),
        ('Запись защиты', bool(video), 'Ссылка из раздела видеозащиты',
         f'{base}/defense', video, ''),
    )
    ready = sum(item[1] for item in materials)
    cards = ''
    for title, present, source, edit_url, url, excerpt in materials:
        action = ('Посмотреть' if present else 'Добавить') if student else 'Посмотреть'
        open_link = (f'<a aria-label="Открыть: {title}" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                     f'Открыть {icon("external")}</a>' if url else '')
        cards += (f'<li class="prep-material{" added" if present else ""}">'
                  f'<div class="prep-material-title">{icon("check" if present else "folder")}<h3>{title}</h3></div>'
                  f'<span class="prep-material-state">{"Добавлено" if present else "Не добавлено"}</span>'
                  f'<p>{source}</p>' + (f'<p class="prep-excerpt">{escape(excerpt)}</p>' if excerpt else '')
                  + f'<div class="prep-material-actions">{open_link}<a aria-label="{title}: {action.lower()} в проекте" href="{edit_url}">{action} в проекте {icon("arrow")}</a></div></li>')
    reviews = ''
    for label, state, comment, previous, url in (
        ('Документация', stage_label(final, next((s['number'] for s in stages if s['state'] != 'done'), None)), final['teacher_comment'], final['state'] == 'review', f'{base}/stages#stage-5-result'),
        ('Видеозащита', DEFENSE_STATES[defense['state']], defense['teacher_comment'], defense['state'] == 'submitted', f'{base}/defense'),
    ):
        reviews += (f'<div class="prep-review"><div><strong>{label}</strong><span>{escape(state)}</span></div>'
                    + (f'<p class="prep-note"><b>{"Замечание к предыдущей сдаче" if previous else "Комментарий учителя"}:</b> {escape(comment)}</p>' if comment else '')
                    + f'<a href="{url}">{"Открыть проверку" if not student else "Открыть работу"} {icon("arrow")}</a></div>')
    return f'''<link rel="stylesheet" href="/static/defense-preparation.css?v=20261027">
        <div class="prep-intro"><p>Соберите материалы и короткие тезисы, чтобы уверенно рассказать о своём проекте.</p>
        <span>План доступен команде и руководителю</span>
        <nav class="prep-jump" aria-label="На этой странице"><a href="#prep-plan-title">План выступления</a><a href="#prep-materials-title">Материалы · {ready}/3</a></nav></div>
        <div class="prep-layout"><section class="prep-plan" aria-labelledby="prep-plan-title">
            <header class="prep-section-heading"><div><span class="prep-eyebrow">Выступление</span><h2 id="prep-plan-title">План выступления</h2></div>
            <span class="prep-counter">Сохранено {filled} из 5 тезисов</span></header>
            <p class="prep-meta">{metadata}</p>
            <p class="prep-guidance">{"Запишите опорные мысли своими словами. Это подсказки для выступления, а не полный текст доклада." if student else "Общие тезисы команды. Ученики заполняют и сохраняют план; учитель может его прочитать."}</p>
            {fields}</section>
        <aside class="prep-sidebar" aria-label="Материалы и проверка">
            <section class="prep-materials" aria-labelledby="prep-materials-title">
                <span class="prep-eyebrow">Готовность материалов</span><h2 id="prep-materials-title">Что уже собрано</h2>
                <p class="prep-count"><strong>{ready}<span> / 3</span></strong><span>материалов добавлено</span></p>
                <ul>{cards}</ul><p class="prep-footnote">Наличие материалов не означает принятие учителем. Откройте ссылки и проверьте права просмотра самостоятельно.</p>
            </section>
            <section class="prep-reviews" aria-labelledby="prep-reviews-title"><h2 id="prep-reviews-title">Решения учителя</h2>{reviews}
                <a class="action-link" href="{base}/history">История работ и решений {icon('history')}</a>
            </section>
            <a class="prep-defense-link" href="{base}/defense">{icon('video')} Перейти к видеозащите {icon('arrow')}</a>
        </aside></div>'''
