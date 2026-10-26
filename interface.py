"""Shared presentation components. No database access or permission decisions."""
from html import escape
from html.parser import HTMLParser


def document_title(html):
    class Heading(HTMLParser):
        def __init__(self):
            super().__init__()
            self.capture = False
            self.complete = False
            self.text = []

        def handle_starttag(self, tag, attrs):
            if tag == 'h1' and not self.complete:
                self.capture = True

        def handle_data(self, data):
            if self.capture:
                self.text.append(data)

        def handle_endtag(self, tag):
            if tag == 'h1' and self.capture:
                self.capture, self.complete = False, True

    heading = Heading()
    heading.feed(html)
    text = ' '.join(' '.join(heading.text).split())[:180]
    return escape(f'{text} — M-Flow' if text else 'M-Flow — проекты и задачи')


_ICONS = {
    'home': '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
    'folder': '<path d="M3 6h6l2 2h10v12H3Z"/>',
    'groups': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    'team': '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 5"/>',
    'results': '<circle cx="12" cy="8" r="5"/><path d="m8 12-2 9 6-3 6 3-2-9"/>',
    'review': '<path d="M9 4H5v17h14V4h-4M9 2h6v5H9ZM8 13l2 2 5-5"/>',
    'settings': '<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
    'profile': '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
    'arrow': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    'menu': '<path d="M4 6h16M4 12h16M4 18h16"/>',
    'close': '<path d="m6 6 12 12M18 6 6 18"/>',
}


def icon(name):
    return ('<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
            'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
            + _ICONS[name] + '</svg>')


def project_art():
    """Decorative diagram, independent of a project's data or subject."""
    return '''<div class="ui-project-art" aria-hidden="true"><svg viewBox="0 0 320 230" fill="none">
        <circle cx="210" cy="128" r="100" stroke="currentColor" opacity=".15"/>
        <circle cx="210" cy="128" r="75" stroke="currentColor" opacity=".12"/>
        <path d="m71 114 91-52 103 58-91 53Z" fill="currentColor" fill-opacity=".08" stroke="currentColor" stroke-opacity=".6"/>
        <path d="m71 114 103 59v40L71 154Zm103 59 91-53v40l-91 53Z" stroke="currentColor" stroke-opacity=".6"/>
        <path d="m108 92 102 59m-65-80 103 59M108 135v40m36-20v40m62-40v40m30-58v40" stroke="currentColor" opacity=".45"/>
        <path d="m133 112 27-16 30 17-27 16Z" fill="currentColor" fill-opacity=".8"/>
        <path d="m133 112 30 17v23l-30-17Zm30 17 27-16v23l-27 16Z" fill="currentColor" fill-opacity=".35" stroke="currentColor"/>
        <circle cx="74" cy="74" r="5" fill="currentColor"/><path d="M74 74h40m-40 0V52" stroke="currentColor" opacity=".5"/>
        <path d="m257 69 8-8m-5-7 8 8M51 183h35m-18-18v35" stroke="currentColor" opacity=".5"/>
        </svg></div>'''


def shell_header(user, path, bell, logout, query=None):
    teacher = user['role'] == 'teacher'
    home = '/teacher' if teacher else '/'
    current = ('groups' if path.startswith('/groups') else 'team' if path.startswith(('/teams', '/skills'))
               else 'settings' if path == '/settings' else 'profile' if path == '/profile'
               else 'projects' if path.startswith('/project/') else 'home' if path in ('/', '/teacher') else '')
    query = query or {}
    selected = (query.get('project') or query.get('result_project', '')) if path == '/' else ''
    if not teacher and path.startswith('/project/'):
        selected = path.split('/')[2]
    project_home = (f'/?project={selected}' if isinstance(selected, str) and selected.isascii()
                    and selected.isdigit() and len(selected) <= 18 and int(selected) > 0 else '/')
    items = ([('home', 'home', 'Кабинет', home), ('projects', 'folder', 'Проекты учеников', '/teacher#teacher-projects'),
              ('groups', 'groups', 'Учебные группы', '/groups'), ('review', 'review', 'Проверка работ', '/teacher#review-queue'),
              ('results', 'results', 'Оценивание', '/teacher#teacher-projects')]
             if teacher else [('home', 'home', 'Главная', '/'), ('projects', 'folder', 'Мои проекты', project_home + '#my-projects'),
                              ('groups', 'groups', 'Учебные группы', '/groups'), ('team', 'team', 'Команды и люди', '/teams'),
                              ('results', 'results', 'Мои результаты', project_home + '#student-results')])
    def links():
        return ''.join(f'<a href="{url}" class="ui-nav-link" data-nav-key="{key}"'
                       + (' aria-current="page"' if current == key else '')
                       + f'>{icon(symbol)}<span>{label}</span></a>' for key, symbol, label, url in items)
    role = 'Учитель' if teacher else 'Ученик'
    logo = f'<a href="{home}" class="brand" aria-label="M-Flow — на главную">M-Flow<span class="ui-brand-dot">.</span></a>'
    secondary = ''.join(f'<a class="ui-nav-link" href="/{key}"'
                        + (' aria-current="page"' if current == key else '')
                        + f'>{icon(key)}<span>{label}</span></a>' for key, label in [('profile', 'Мой профиль'), ('settings', 'Настройки')])
    title = next((label for key, _, label, _ in items if key == current),
                 {'/profile':'Мой профиль', '/settings':'Настройки', '/notifications':'Уведомления',
                  '/supervision':'Руководитель проекта'}.get(path, 'Рабочее пространство'))
    return f'''<aside class="ui-sidebar" aria-label="Боковая навигация">
        <div class="ui-sidebar-brand">{logo}</div><p class="ui-workspace-label">ПРОСТРАНСТВО {"УЧИТЕЛЯ" if teacher else "УЧЕНИКА"}</p>
        <nav class="ui-primary-nav" aria-label="Основные разделы">{links()}</nav>
        <div class="ui-sidebar-bottom"><p class="ui-sidebar-motto">От первой идеи<br>до своего результата.<small>Это твой M-Flow.</small></p>{secondary}
        <span class="ui-signature">СОЗДАВАЙ. ПРОБУЙ. РАСТИ.</span></div></aside>
    <header class="main-header simple-header" data-interface-shell="{user['role']}">
        <div class="ui-header-start"><details class="ui-mobile-menu">
            <summary aria-controls="ui-mobile-panel">{icon('menu')}<span>Меню</span></summary>
            <div class="ui-mobile-panel" id="ui-mobile-panel" aria-label="Навигация"><div class="ui-mobile-panel-top"><span class="ui-mobile-wordmark">M-Flow<span>.</span></span><button type="button" class="ui-icon-button" data-close-navigation aria-label="Закрыть меню">{icon('close')}</button></div>
            <p class="ui-workspace-label">{role}</p><nav aria-label="Мобильные разделы">{links()}{secondary}</nav></div>
            <button class="ui-mobile-backdrop" type="button" data-close-navigation aria-label="Закрыть навигацию"></button>
        </details>{logo}<span class="ui-header-context">Моё пространство<span>/</span>{title}</span></div>
        <div class="header-actions">{bell}<details class="account-menu"><summary aria-controls="account-panel">Мой аккаунт</summary>
            <div class="account-panel" id="account-panel"><div class="account-identity"><strong>{escape(user['username'])}</strong><small>{role}</small></div>
            <a href="/profile">Мой профиль</a><a href="/settings">Настройки</a>{logout}</div></details></div></header>'''
