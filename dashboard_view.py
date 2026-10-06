"""Presentation only: no database, roles or request mutations."""
from html import escape


def student_dashboard(user, projects, token, header, messages):
    total = sum(p['total'] for p in projects)
    done = sum(p['done'] for p in projects)
    active = sum(p['active'] for p in projects)
    overdue = sum(p['overdue'] for p in projects)
    progress = round(done / total * 100) if total else 0
    cards = []
    for p in projects:
        percent = round(p['done'] / p['total'] * 100) if p['total'] else 0
        caption = 'Пока нет задач' if not p['total'] else 'Все задачи выполнены' if p['done'] == p['total'] else 'Работа продолжается'
        deadline = f'<span class="tile-alert">Просрочено: {p["overdue"]}</span>' if p['overdue'] else ''
        cards.append(f'''<a class="project-row project-tile" href="/project/{p['id']}">
            <div class="tile-top"><span class="tile-folder" aria-hidden="true">↗</span><span>{caption}</span>{deadline}</div>
            <h3>{escape(p['name'])}</h3><div class="tile-bottom"><span>{p['done']} из {p['total']} задач выполнено</span><strong>{percent}%</strong></div>
            <div class="mini-progress"><div style="width:{percent}%"></div></div></a>''')
    project_cards = ''.join(cards) or '''<div class="empty workspace-empty"><img src="/static/project-kit.svg" alt="" width="480" height="360">
        <div><span class="eyebrow">ЗДЕСЬ НАЧИНАЕТСЯ ТВОЙ ПРОЕКТ</span><h3>Пока чистый лист.</h3>
        <p>Создай первый проект, добавь команду и учителя. Дальше — шаг за шагом.</p><a class="btn" href="#project-create" data-open-project>+ Новый проект</a></div></div>'''
    return f'''<div class="container workspace student-workspace">{header}<main id="main-content">
        <div class="workspace-intro"><span>ТВОЁ РАБОЧЕЕ ПРОСТРАНСТВО</span><span>M-Flow / проекты</span></div>
        <section class="workspace-bento">
            <div class="workspace-hero"><div class="hero-copy"><span class="hero-kicker">Привет, {escape(user['username'])}</span>
                <h1>От идеи —<br>к результату.</h1><p>Твои проекты, команда и обратная связь учителя. Всё, что нужно для следующего шага.</p>
                <a class="btn" href="#project-create" data-open-project>+ Новый проект <span aria-hidden="true">↗</span></a></div>
                <img class="hero-art" src="/static/project-kit.svg" alt="" width="480" height="360"></div>
            <div class="workspace-progress"><span class="eyebrow">ПРОГРЕСС ЗАДАЧ</span>
                <div class="progress-orbit"><svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="52" class="orbit-track"/><circle cx="60" cy="60" r="52" class="orbit-fill" stroke-dasharray="{progress * 3.267} 326.7"/></svg><strong>{progress}%</strong></div>
                <p>{done} из {total} задач выполнено</p><small>Этапы проекта проверяются отдельно</small></div>
            <a class="workspace-shortcut" href="/teams"><span class="shortcut-icon" aria-hidden="true">↗</span><div><h2>Сильнее вместе.</h2><p>Найди команду по навыкам, а не по классу.</p></div></a>
        </section>{messages}
        <section class="stats-line dashboard-stats" aria-label="Показатели проектов"><div><strong>{len(projects)}</strong><span>Проектов</span></div><div><strong>{total}</strong><span>Всего задач</span></div><div><strong>{active}</strong><span>В работе</span></div><div><strong>{overdue}</strong><span>Просрочено</span></div></section>
        <section class="workspace-projects"><div class="section-title"><div><span class="eyebrow">ПРОДОЛЖАЙ С ТОГО, ГДЕ ОСТАНОВИЛСЯ</span><h2>Мои проекты</h2><p>Проекты, к которым у тебя есть доступ</p></div></div>
            <details id="project-create" class="project-create"><summary>+ Новый проект</summary><form id="project-form" class="new-project show" action="/add_project" method="post">
                <input type="hidden" name="csrf_token" value="{escape(token, quote=True)}"><label for="project-name">Название проекта</label><input id="project-name" name="name" placeholder="Название проекта" maxlength="80" required><button type="submit">Создать</button></form></details>
            <div class="projects-list project-grid">{project_cards}</div></section>
        <section class="workspace-guide"><div><span class="eyebrow">ПОНЯТНЫЙ МАРШРУТ</span><h2>Большая работа.<br>Пять небольших шагов.</h2><p>Выбери тему, пройди три этапа работы и подготовь документацию. Учитель поможет на каждом этапе.</p></div>
            <ol class="route-strip"><li><b>01</b><span>Тема</span></li><li><b>02</b><span>Работа</span></li><li><b>03</b><span>Работа</span></li><li><b>04</b><span>Работа</span></li><li><b>05</b><span>Документы</span></li></ol></section>
        <footer class="workspace-footer"><span>M-Flow <small>by Minich</small></span><a href="/settings">Настройки оформления ↗</a></footer>
    </main></div>'''
