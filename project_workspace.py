"""Read-only project overview. Presentation never grants mutation permissions."""
from datetime import date
from html import escape

from grading import MAX_SCORE, load_scores, summarize
from project_stages import stage_summary, stage_label, stage_needs_revision, STAGE_DESCRIPTIONS
from video_defense import DEFENSE_STATES


def task_deadline(tasks, today=None):
    today = today or date.today()
    deadlines = []
    for task in tasks:
        if task['status'] == 'done' or not task['deadline']:
            continue
        try:
            due = date.fromisoformat(task['deadline'])
        except ValueError:
            continue
        if due.isoformat() == task['deadline']:
            deadlines.append(due)
    if not deadlines:
        return 'Нет незавершённых задач со сроком'
    due = min(deadlines)
    label = 'Просроченный срок' if due < today else 'Срок сегодня' if due == today else 'Ближайший срок'
    return f'{label}: {due.strftime("%d.%m.%Y")}'


def next_step(stages, role, owner, has_teacher, defense, has_published=False):
    """Actions are links to existing forms, not duplicate POST workflows."""
    _, current = stage_summary(stages)
    def step(key, title, text, target=None, action=None):
        return dict(key=key, title=title, text=text, target=target, action=action)
    if role == 'student' and not has_teacher:
        editable = bool(current and current['state'] in ('todo', 'progress'))
        return step('teacher-missing', 'В проекте пока нет учителя',
                    'Добавь учителя по логину в разделе участников. Черновики доступны, отправка на проверку — после подключения учителя.' if owner else
                    ('Попроси владельца добавить учителя по логину. Можно готовить черновик этапа, но отправка на проверку пока недоступна.' if editable else
                     'Попроси владельца добавить учителя по логину. Отправленный результат нельзя менять до решения учителя.' if current else
                     'Попроси владельца добавить учителя по логину, чтобы продолжить проверку защиты и оценивание.'),
                    '#project-members' if owner else (f'stages#stage-{current["number"]}' if editable else None),
                    'Добавить учителя' if owner else ('Подготовить черновик' if editable else None))
    if role == 'teacher':
        if current and current['state'] == 'review':
            return step('review', 'Результат ждёт проверки', 'Прочитайте результат текущего этапа. Примите его или верните ученикам с пояснением.',
                        f'stages#stage-{current["number"]}', 'Проверить этап')
        if defense['state'] == 'submitted':
            return step('defense-review', 'Видеозащита ждёт проверки', 'Откройте запись выступления и сохраните решение по защите.', 'defense', 'Проверить видеозащиту')
        if current:
            if stage_needs_revision(current):
                return step('await-revision', 'Ожидается исправленный результат', 'Ученики дорабатывают этап по замечанию учителя. Проверка станет доступна после повторной отправки.',
                            f'stages#stage-{current["number"]}', 'Посмотреть замечание')
            return step('await-result', 'Ученики готовят результат', 'Этап ещё не отправлен. Можно посмотреть задачи и отчёты; принять черновик вместо отправленного результата нельзя.',
                        '#task-board', 'Посмотреть задачи и отчёты')
        return step('stages-accepted', 'Все пять этапов приняты',
                    'Видеозащита принята. Проверьте опубликованные оценки участников.' if defense['state'] == 'accepted' else
                    'Приём этапов не заменяет защиту и оценивание. Запись пока не отправлена или ожидается её доработка.',
                    'assessment' if defense['state'] == 'accepted' else 'defense',
                    'Перейти к оцениванию' if defense['state'] == 'accepted' else 'Посмотреть видеозащиту')
    if current:
        target = f'stages#stage-{current["number"]}'
        if current['state'] == 'review':
            return step('await-review', 'Результат у учителя', 'Этап отправлен на проверку. Пока результат нельзя менять; можно продолжить работу с задачами.', '#task-board', 'Перейти к задачам')
        if stage_needs_revision(current):
            return step('revise', 'Доработай результат этапа', 'Учитель вернул работу с замечанием. Исправь результат и отправь его повторно.', target, 'Перейти к доработке')
        if current['result'].strip() and (current['number'] != 5 or current['presentation_url']):
            return step('ready', 'Черновик готов к отправке', 'Проверь результат и отправь его учителю на странице этапа. Сохранённый черновик ещё не находится на проверке.', target, 'Перейти к отправке')
        if current['state'] == 'todo':
            return step('start', 'Согласуй тему с учителем' if current['number'] == 1 else 'Начни следующий этап', STAGE_DESCRIPTIONS[current['number']], target, 'Подготовить результат')
        return step('work', 'Продолжи работу над этапом', STAGE_DESCRIPTIONS[current['number']], target, 'Дополнить результат')
    if defense['state'] in ('draft', 'returned'):
        return step('defense-prepare', 'Этапы приняты — подготовь видеозащиту' if defense['state'] == 'draft' else 'Исправь запись защиты',
                    'Добавь ссылку на запись выступления и проверь доступ учителя. Видеозащита проверяется отдельно от этапов.' if defense['state'] == 'draft' else
                    'Открой видеозащиту, прочитай замечание учителя и отправь исправленную запись.', 'defense', 'Подготовить видеозащиту' if defense['state'] == 'draft' else 'Доработать видеозащиту')
    if defense['state'] == 'submitted':
        return step('defense-wait', 'Этапы приняты, защита на проверке', 'Запись ожидает решения учителя. Принятые этапы не означают, что защита и оценивание завершены.', 'defense', 'Посмотреть состояние защиты')
    return step('assessment', 'Этапы и видеозащита приняты', 'Твои опубликованные баллы доступны в оценивании.' if has_published else
                'Опубликованной оценки пока нет. Дождись решения учителя; баллы не выставляются автоматически.', 'assessment', 'Посмотреть свои оценки')


def render_workspace(project_id, name, user, owner, members, stages, defense, assessments, tasks, completed_tasks, progress, overdue):
    done, current = stage_summary(stages)
    has_teacher = any(member['role'] == 'teacher' for member in members)
    step = next_step(stages, user['role'], owner, has_teacher, defense, bool(assessments))
    base = f'/project/{project_id}'
    target = step['target']
    action_url = target if target and target.startswith('#') else f'{base}/{target}' if target else None
    action = f'<a class="btn" href="{action_url}">{step["action"]} <span aria-hidden="true">↗</span></a>' if action_url else ''
    member_list = ''.join(f'<li>{escape(m["username"])} <span>{"Учитель" if m["role"] == "teacher" else "Ученик"}</span></li>' for m in members)
    names = ', '.join(m['username'] for m in members[:3]) + (' и другие' if len(members) > 3 else '')
    current_name = f'Этап {current["number"]} / {escape(current["title"])}' if current else 'Все пять этапов приняты'
    current_label = stage_label(current) if current else 'Этапы приняты'
    feedback = ''
    if current and stage_needs_revision(current):
        reviewer = escape(current.get('reviewer_name') or 'Учитель проекта')
        updated = f' · Последнее обновление этапа: {escape(current["updated_at"])} UTC' if current['updated_at'] else ''
        feedback = f'''<aside class="journey-feedback" aria-label="Замечание учителя"><strong>Что исправить · этап {current['number']}</strong>
            <small>{reviewer}{updated}</small><p tabindex="0" aria-label="Замечание учителя; длинный текст можно прокрутить">{escape(current['teacher_comment'])}</p>
            <a href="{base}/stages#stage-{current['number']}">{'Перейти к доработке' if user['role'] == 'student' else 'Открыть результат и замечание'} →</a></aside>'''
    route = ''
    for stage in stages:
        locked = bool(current and stage['number'] > current['number'])
        label = stage_label(stage, current['number'] if current else None)
        title = escape(stage['title'])
        title_html = title if locked else f'<a href="{base}/stages#stage-{stage["number"]}">{title}</a>'
        explanation = f'Откроется после принятия этапа {stage["number"] - 1} учителем.' if locked else STAGE_DESCRIPTIONS[stage['number']]
        css = 'locked' if locked else 'accepted' if stage['state'] == 'done' else 'current'
        marker = '✓' if css == 'accepted' else str(stage['number'])
        route += f'''<li class="route-stage {css}" {'aria-current="step"' if css == 'current' else ''}>
            <span class="route-marker" aria-hidden="true">{marker}</span><div><strong>{title_html}</strong>
            <span class="route-state">{label}</span><p>{explanation}</p></div></li>'''
    if user['role'] == 'teacher':
        grading = f'Опубликовано ваших оценок: {len(assessments)}' if assessments else 'Ваших опубликованных оценок пока нет'
    else:
        grades = []
        for row in assessments:
            try:
                scores = load_scores(row)
                if not isinstance(scores, dict):
                    raise ValueError('Invalid stored score structure')
                total, grade = summarize(scores)
                grades.append(f'{total} / {MAX_SCORE} · оценка {grade if grade is not None else "не определена"} · {escape(row["teacher_name"])}')
            except (ValueError, TypeError):
                grades.append('Оценка опубликована · подробности в разделе оценивания')
        grading = '<br>'.join(grades) or 'Ваша оценка ещё не опубликована'
    defense_label = DEFENSE_STATES[defense['state']] if defense['revision'] else 'Запись ещё не добавлена'
    return f'''<section class="project-overview" aria-label="Маршрут проекта">
        <div class="journey-heading"><div><span class="eyebrow">ПРОЕКТ / ОТ ТЕМЫ ДО ЗАЩИТЫ</span><h1>{escape(name)}</h1>
            <details class="journey-members"><summary>Участники: {len(members)} · {escape(names) or 'Команда пока не сформирована'}</summary><ul>{member_list}</ul></details></div>
            <a class="action-link" href="#task-board">К доске задач ↓</a></div>
        <div class="journey-next"><div class="journey-context"><span>{current_name}</span><strong>{current_label}</strong>
            <small data-task-deadline>{task_deadline(tasks)}</small><small>Срок относится к задаче, не к этапу</small></div>
            <div class="journey-action" data-next-step="{step['key']}"><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h2>{step['title']}</h2><p>{step['text']}</p>{action}</div></div>
        {feedback}
        <div class="journey-progress"><div><div><strong>Прогресс задач</strong><b data-project-progress>{progress}%</b></div>
            <p data-project-progress-text>{completed_tasks} из {len(tasks)} задач выполнено</p><div class="project-progress"><div data-project-progress-fill style="width:{progress}%"></div></div></div>
            <div><div><strong>Этапы, принятые учителем</strong><b>{done * 20}%</b></div><p>Принято {done} из 5 этапов</p><div class="project-progress"><div style="width:{done * 20}%"></div></div></div></div>
        <details class="journey-route"><summary>Маршрут из пяти этапов <span>Принято {done} / 5</span></summary>
            <p class="journey-explanation">Задачи помогают выполнять работу. Этап принимается только после решения учителя; защита и оценки проверяются отдельно.</p><ol>{route}</ol></details>
        <nav class="journey-links" aria-label="Разделы проекта"><a href="#task-board"><strong>Задачи и отчёты ↗</strong><span data-workspace-task-summary>{completed_tasks} из {len(tasks)} · просрочено {overdue}</span></a>
            <a href="{base}/stages"><strong>Этапы ↗</strong><span>Принято {done} из 5</span></a>
            <a href="{base}/team"><strong>Команда ↗</strong><span>Участников: {len(members)}</span></a>
            <a href="{base}/defense"><strong>Видеозащита ↗</strong><span>{defense_label}</span></a>
            <a href="{base}/assessment"><strong>Оценивание ↗</strong><span>{grading}</span></a></nav>
    </section>'''
