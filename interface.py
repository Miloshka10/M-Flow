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


# One source for server SVGs and the generated decorative CSS masks.
_ICONS = {
    'home': '<path d="m3.5 10 7.3-6a1.9 1.9 0 0 1 2.4 0l7.3 6"/><path d="M5.5 8.4V19a1.5 1.5 0 0 0 1.5 1.5h10a1.5 1.5 0 0 0 1.5-1.5V8.4M9.5 20.5V14a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v6.5"/>',
    'folder': '<path d="M20.5 9V7.5A1.5 1.5 0 0 0 19 6h-7L9.8 3.8a1 1 0 0 0-.7-.3H5A1.5 1.5 0 0 0 3.5 5v14A1.5 1.5 0 0 0 5 20.5h14a1.5 1.5 0 0 0 1.5-1.5V9H3.5"/><path d="M14 12h3.5M14 15h3.5"/>',
    'groups': '<rect x="2.5" y="3.5" width="19" height="17" rx="3"/><circle cx="12" cy="9.5" r="2.25"/><path d="M7.5 17v-.5a4.5 4.5 0 0 1 9 0v.5M6 9.5v.01M18 9.5v.01M5 15v-1a2 2 0 0 1 1-1.7M19 15v-1a2 2 0 0 0-1-1.7"/>',
    'team': '<circle cx="8.5" cy="8" r="3"/><path d="M2.5 20v-1.5a6 6 0 0 1 12 0V20M16 5.5a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4.5 5v1"/>',
    'results': '<path d="M14 3.5H6A1.5 1.5 0 0 0 4.5 5v14A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5V9L14 3.5v4A1.5 1.5 0 0 0 15.5 9h4"/><path d="m8.5 14.5 2.5 2.5 4.5-4.5"/>',
    'review': '<rect x="5" y="4.5" width="14" height="16" rx="2"/><rect x="8.5" y="2.5" width="7" height="4" rx="1.5"/><path d="m8.5 13.5 2.5 2.5 4.5-4.5"/>',
    'settings': '<path d="M9.62 5.10L10.40 2.94L13.60 2.94L14.38 5.10L15.20 5.44L17.28 4.46L19.54 6.72L18.56 8.80L18.90 9.62L21.06 10.40L21.06 13.60L18.90 14.38L18.56 15.20L19.54 17.28L17.28 19.54L15.20 18.56L14.38 18.90L13.60 21.06L10.40 21.06L9.62 18.90L8.80 18.56L6.72 19.54L4.46 17.28L5.44 15.20L5.10 14.38L2.94 13.60L2.94 10.40L5.10 9.62L5.44 8.80L4.46 6.72L6.72 4.46L8.80 5.44Z"/><circle cx="12" cy="12" r="3"/>',
    'profile': '<circle cx="12" cy="8" r="3.5"/><path d="M5 20v-1a7 7 0 0 1 14 0v1"/>',
    'assessment': '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 16v-3M12 16V8M16 16v-5"/>',
    'arrow': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    'arrow-left': '<path d="M20 12H4m6-6-6 6 6 6"/>',
    'external': '<path d="M14 4h6v6m0-6-9 9M10 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-4"/>',
    'menu': '<path d="M4 6h16M4 12h16M4 18h16"/>',
    'close': '<path d="m6 6 12 12M18 6 6 18"/>',
    'bell': '<path d="M5 16.5h14l-1.5-3V9a5.5 5.5 0 0 0-11 0v4.5L5 16.5ZM10 20a2.5 2.5 0 0 0 4 0"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    'filter': '<path d="M4 7h1.5M10.5 7H20M4 17h9.5M18.5 17H20"/><circle cx="8" cy="7" r="2.5"/><circle cx="16" cy="17" r="2.5"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'edit': '<path d="m14 5 5 5M4 20l5-1 11-11a2.1 2.1 0 0 0-5-5L4 14v6Z"/>',
    'save': '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2ZM7 3v6h9V3M7 21v-7h10v7"/>',
    'send': '<path d="m3 10 18-7-7 18-3-8-8-3Zm8 3L21 3"/>',
    'check': '<path d="m5 12 4.5 4.5L19 7"/>',
    'return': '<path d="M9 5 4 10l5 5M4 10h9a6 6 0 0 1 0 12"/>',
    'trash': '<path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
    'logout': '<path d="M9 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4M14 7l5 5-5 5M9 12h10"/>',
    'history': '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
    'tasks': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m6 9 1.5 1.5L10 8M13 9h5m-12 6 1.5 1.5L10 14m3 1h5"/>',
    'stages': '<path d="M4 20h5v-5h5v-5h6V4M15 4h5v5"/>',
    'overview': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 10h18M10 10v11"/>',
    'video': '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    'link': '<path d="m10 13 4-4M8.5 15l-1 1a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 5 0m1 2 1-1a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-5 0"/>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 11h18M7 15h2M13 15h4"/>',
    'archive': '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v12h14V8M9 12h6"/>',
    'chevron': '<path d="m6 9 6 6 6-6"/>',
}


def icon(name, navigation=False):
    classes = 'ui-icon ui-nav-icon' if navigation else 'ui-icon'
    return (f'<svg class="{classes}" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" '
            'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'
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
              ('results', 'assessment', 'Оценивание', '/teacher#teacher-projects')]
             if teacher else [('home', 'home', 'Главная', '/'), ('projects', 'folder', 'Мои проекты', project_home + '#my-projects'),
                              ('groups', 'groups', 'Учебные группы', '/groups'), ('team', 'team', 'Команды и люди', '/teams'),
                              ('results', 'results', 'Мои результаты', project_home + '#student-results')])
    def links():
        return ''.join(f'<a href="{url}" class="ui-nav-link" data-nav-key="{key}"'
                       + (' aria-current="page"' if current == key else '')
                       + f'>{icon(symbol, navigation=True)}<span>{label}</span></a>' for key, symbol, label, url in items)
    role = 'Учитель' if teacher else 'Ученик'
    logo = f'<a href="{home}" class="brand" aria-label="M-Flow — на главную">M-Flow<span class="ui-brand-dot">.</span></a>'
    secondary = ''.join(f'<a class="ui-nav-link" href="/{key}"'
                        + (' aria-current="page"' if current == key else '')
                        + f'>{icon(key, navigation=True)}<span>{label}</span></a>' for key, label in [('profile', 'Мой профиль'), ('settings', 'Настройки')])
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
            <a href="/profile">{icon('profile')}Мой профиль</a><a href="/settings">{icon('settings')}Настройки</a>{logout}</div></details></div></header>'''
