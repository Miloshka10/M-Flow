"""Settings navigation and short help for the existing product workflows."""
from interface import icon
from notifications import ROLE_NOTIFICATION_GROUPS


SECTIONS = (
    ('appearance', 'settings', ('Язык и оформление', 'Language and appearance')),
    ('notifications', 'bell', ('Уведомления', 'Notifications')),
    ('account', 'profile', ('Аккаунт и безопасность', 'Account and security')),
    ('help', 'tasks', ('Помощь', 'Help')),
)

QUESTIONS = {
    'student': (
        (('Как подключить учителя?', 'How do I connect a teacher?'),
         ('Попроси у учителя одноразовый код. В разделе руководства введи код и выбери проект. Учитель подтвердит подключение.',
          'Ask your teacher for a one-time code. Enter it in Supervision, choose the project and wait for your teacher to confirm.'),
         '/supervision', ('Руководитель проекта', 'Open supervision')),
        (('Как исправить и отправить этап повторно?', 'How do I resubmit a stage?'),
         ('На Главной открой проект со статусом «Нужна доработка». Прочитай замечание, нажми «Доработать результат» и отправь исправленную работу.',
          'On Home, open the project marked as returned. Read the feedback, open the result editor and submit the updated work.'),
         '/', ('Открыть Главную', 'Open Home')),
        (('Где посмотреть решения учителя и оценки?', 'Where are my feedback and grades?'),
         ('«Мои результаты» показывает статусы, замечания и опубликованные личные оценки.',
          'My results shows submission statuses, teacher feedback and your published grades.'),
         '/#student-results', ('Мои результаты', 'Open My results')),
        (('Как вступить в учебную группу?', 'How do I join a class group?'),
         ('Попроси у учителя код группы и введи его в разделе «Учебные группы».',
          'Ask your teacher for the group code and enter it under Class groups.'),
         '/groups#group-code', ('Ввести код группы', 'Enter a group code')),
    ),
    'teacher': (
        (('Где увидеть новые и повторные сдачи?', 'Where are new submissions and resubmissions?'),
         ('Открой центр проверки и выбери работу. Можно принять результат или вернуть его с замечанием.',
          'Open the Review center and select a submission. You can accept the work or return it with feedback.'),
         '/teacher/review', ('Центр проверки', 'Open the Review center')),
        (('Как стать руководителем проекта?', 'How do I supervise a project?'),
         ('Создайте одноразовый код в разделе руководства и передайте его владельцу проекта. После его запроса подтвердите подключение.',
          'Create a one-time code under Supervision and give it to the project owner. Confirm once they request supervision.'),
         '/supervision', ('Руководство проектами', 'Open supervision')),
        (('Как создать учебную группу?', 'How do I create a class group?'),
         ('Создайте группу в разделе «Учебные группы» и передайте ученикам её код.',
          'Create a group under Class groups and share its code with your students.'),
         '/groups', ('Учебные группы', 'Open Class groups')),
    ),
    None: (
        (('Как восстановить доступ?', 'How do I recover access?'),
         ('На странице восстановления укажи логин и почту, которую подтвердил в аккаунте.',
          'On the recovery page, enter your username and the email address you verified on your account.'),
         '/forgot-password', ('Восстановить доступ', 'Recover access')),
        (('Где войти учителю?', 'Where do teachers sign in?'),
         ('На форме входа выбери «Я учитель» и войди в кабинет учителя.',
          'Choose “I’m a teacher” on the sign-in form to open the teacher sign-in page.'),
         '/teacher/login', ('Вход учителя', 'Teacher sign-in')),
    ),
}

NOTIFICATION_LABELS = {
    'student': {
        'work': ('Решения по этапам и видеозащите', 'Decisions on stages and video defenses'),
        'assessment': ('Опубликованные оценки', 'Published grades'),
        'supervision': ('Согласования руководителя', 'Supervision updates'),
    },
    'teacher': {
        'work': ('Новые и повторные сдачи', 'New submissions and resubmissions'),
        'report': ('Отчёты по задачам', 'Task reports'),
        'supervision': ('Согласования руководителя', 'Supervision updates'),
    },
}


def render_settings(header, user, section, lang, muted, csrf, saved):
    en = lang == 'en'
    role = user['role'] if user else None
    available = [item for item in SECTIONS if user or item[0] in ('appearance', 'help')]
    if section not in {key for key, _, _ in available}:
        section = 'appearance'
    title = next(labels[en] for key, _, labels in available if key == section)
    links = ''.join(f'<a href="/settings?section={key}"'
                    + (' aria-current="page"' if key == section else '')
                    + f'>{icon(symbol)}<span>{labels[en]}</span></a>' for key, symbol, labels in available)
    heading = 'Settings' if en else 'Настройки'
    if section == 'appearance':
        panel = render_appearance(en, csrf)
    elif section == 'account':
        account_links = (('/profile', 'profile', ('Профиль', 'Profile')),
                         ('/profile#change-password', 'settings', ('Сменить пароль', 'Change password')),
                         ('/profile/email', 'link', ('Почта для восстановления', 'Recovery email')))
        panel = '<div class="settings-links">' + ''.join(
            f'<a href="{href}">{icon(symbol)}<span>{labels[en]}</span>{icon("arrow")}</a>'
            for href, symbol, labels in account_links) + '</div>'
    elif section == 'notifications':
        panel = render_notification_settings(role, en, muted, csrf, saved)
    else:
        panel = ''.join(f'<details class="settings-answer"><summary>{question[en]}</summary>'
                        f'<p>{answer[en]}</p><a class="action-link" href="{href}">{label[en]} {icon("arrow")}</a></details>'
                        for question, answer, href, label in QUESTIONS[role])
    return f'''<div class="container">{header}<main class="settings-page" lang="{lang}"><h1>{heading}</h1>
        <div class="settings-layout settings-center">
            <nav class="settings-sections" aria-label="{heading}">{links}</nav>
            <section class="card settings-card" aria-labelledby="settings-section-title">
            <h2 id="settings-section-title">{title}</h2>{panel}</section>
        </div></main></div>'''


def render_appearance(en, csrf):
    light, dark, system = ('Light', 'Dark', 'System') if en else ('Светлая', 'Тёмная', 'Как в системе')
    return f'''<form class="settings-language" method="post" action="/settings/language">{csrf}
        <label for="settings-language">{'Language for settings and help' if en else 'Язык настроек и справки'}</label>
        <div class="settings-language-controls"><select id="settings-language" name="language">
            <option value="ru" lang="ru"{' selected' if not en else ''}>Русский</option>
            <option value="en" lang="en"{' selected' if en else ''}>English</option>
        </select><button class="secondary" type="submit">{'Apply language' if en else 'Применить язык'}</button></div>
        <p class="settings-hint">{'Other pages are currently in Russian.' if en else 'Остальные страницы пока на русском.'}</p>
        </form>
        <form id="site-preferences">
            <fieldset id="site-theme" class="settings-theme">
                <legend>{'Site theme' if en else 'Тема сайта'}</legend>
                <div class="settings-themes">
                    <label><input type="radio" name="theme" value="light">{light}</label>
                    <label><input type="radio" name="theme" value="dark">{dark}</label>
                    <label><input type="radio" name="theme" value="system" checked>{system}</label>
                </div>
            </fieldset>
            <label class="settings-checkbox" for="site-animations">
                <input id="site-animations" name="animations" type="checkbox" aria-describedby="animations-hint" checked>
                {'Smooth animations' if en else 'Плавные анимации'}</label>
            <p class="settings-hint" id="animations-hint">{'Follows your device’s reduced-motion setting.' if en else 'Учитываем настройку уменьшения движения на устройстве.'}</p>
            <button type="button" class="secondary" id="reset-preferences" data-ui-icon="return">{'Reset appearance' if en else 'Сбросить оформление'}</button>
            <p id="preferences-status" role="status" aria-live="polite"
                {'data-saved-label="Settings saved in this browser." data-storage-error="Settings applied, but the browser could not save them."' if en else ''}></p>
            <noscript><p class="error">{'Enable JavaScript to change appearance.' if en else 'Для изменения оформления включите JavaScript.'}</p></noscript>
        </form>'''


def render_notification_settings(role, en, muted, csrf, saved):
    options = ''.join(f'<label class="settings-checkbox"><input type="checkbox" name="notification" '
                      f'value="{category}"{" checked" if category not in muted else ""}>'
                      f'{NOTIFICATION_LABELS[role][category][en]}</label>' for category in ROLE_NOTIFICATION_GROUPS[role])
    hint = 'Show in the notification menu' if en else 'Показывать в меню уведомлений'
    status = (f'<p role="status" class="success">{"Settings saved." if en else "Настройки сохранены."}</p>' if saved else '')
    return f'''{status}<p class="settings-hint">{hint}</p><form method="post" action="/settings/notifications">{csrf}
        {options}<button type="submit" class="settings-save">{'Save' if en else 'Сохранить'}</button></form>
        <a class="action-link" href="/notifications">{'All notifications' if en else 'Все уведомления'} {icon("arrow")}</a>'''
