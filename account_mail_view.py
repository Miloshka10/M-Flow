"""Presentation for account access; handlers and server checks remain authoritative."""
from html import escape
import re
from interface import APPEARANCE_VERSION, access_header
from ui_components import state_badge

ACCESS_VERSION = '20261106-access'


def password_control(input_id, name, label, autocomplete, limits='', hint=''):
    subject = 'повтор пароля' if name == 'repeat_password' else 'текущий пароль' if name in ('old_password',) else 'пароль'
    described = f' aria-describedby="{input_id}-hint"' if hint else ''
    note = f'<p class="auth-hint" id="{input_id}-hint">{escape(hint)}</p>' if hint else ''
    return f'''<div class="access-field"><label for="{input_id}">{escape(label)}</label><div class="auth-password-control">
        <input id="{input_id}" name="{name}" type="password" autocomplete="{autocomplete}"{limits}{described} required>
        <button class="auth-password-toggle" type="button" aria-controls="{input_id}" aria-pressed="false" aria-label="Показать {subject}" hidden>Показать</button></div>{note}</div>'''


def account_assets():
    return f'<link rel="stylesheet" href="/static/auth.css?v={APPEARANCE_VERSION}"><script src="/static/auth.js?v={ACCESS_VERSION}" defer></script><script src="/static/account-mail.js?v={ACCESS_VERSION}" defer></script>'


def token_field(value=''):
    return f'''<details class="access-manual-token" data-token-entry open><summary>Ввести код из письма вручную</summary><div class="access-field">
        <label data-token-label for="mail-token">Код из письма</label><input id="mail-token" name="token" type="password" autocomplete="off" minlength="43" maxlength="43" value="{escape(value, quote=True)}" aria-describedby="mail-token-help" required>
        <p class="auth-hint" id="mail-token-help">Скопируйте код целиком из письма M-Flow. Никому его не передавайте.</p></div></details>'''


def _password_controls(fields):
    # Only ordinary password fields receive visibility controls, never the email capability.
    def wrap(match):
        tag, input_id = match.group(0), match.group(1)
        if input_id == 'mail-token' or not re.search(r'name="(?:password|repeat_password)"', tag):
            return tag
        return f'<div class="auth-password-control">{tag}<button class="auth-password-toggle" type="button" aria-controls="{input_id}" aria-pressed="false" aria-label="Показать пароль" hidden>Показать</button></div>'
    return re.sub(r'<input id="([^"]+)"[^>]*type="password"[^>]*>', wrap, fields)


def public_form(title, intro, fields, token, button, error='', notice='', token_form=False, ready=True,
                state='form', login_url='/login'):
    kind = 'reset' if title == 'Новый пароль' else 'reset-success' if title == 'Пароль изменён' else 'confirm' if title == 'Подтверждение почты' else 'forgot'
    headings = {'sent': 'Проверьте почту', 'confirmed': 'Почта подтверждена',
                'pending-confirmation': 'Осталось второе подтверждение', 'unavailable': 'Письма пока недоступны'}
    heading = headings.get(state, title)
    if state == 'sent':
        intro = 'Для продолжения понадобится ссылка из письма M-Flow.'
    elif state == 'confirmed':
        intro = 'Вернитесь в аккаунт — статус почты уже обновлён.'
    elif state == 'pending-confirmation':
        intro = 'Откройте письмо во втором ящике. До завершения смены действует прежний адрес.'
    elif state == 'unavailable':
        intro = 'Вход по паролю продолжает работать. Отправить письмо сейчас не получится.'
    feedback = f'<div class="error auth-error" role="alert" tabindex="-1">{escape(error)}</div>' if error else ''
    if notice:
        tone = 'success' if state == 'confirmed' else 'warning' if state == 'unavailable' else 'info'
        css = 'success' if tone == 'success' else 'ui-notice'
        feedback += f'<div class="{css} access-feedback" data-ui-tone="{tone}" role="status">{escape(notice)}</div>'
    fields = _password_controls(fields)
    form = ''
    if ready:
        token_help = ('<div class="ui-notice access-token-help" data-token-help><strong>Откройте ссылку из письма</strong>'
                      '<p>Она подставит код автоматически. Если ссылка не открывается, введите код из того же письма ниже.</p></div>') if token_form else ''
        form = (f'<form method="post" class="access-form" {("data-email-token-form" if token_form else "")}>'
                f'<input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">{token_help}{fields}'
                f'<button type="submit" class="auth-submit" data-pending-label="{("Отправляем…" if kind == "forgot" else "Сохраняем…")}">{escape(button)}</button></form>'
                '<p class="auth-submit-status" role="status" aria-live="polite"></p>')
    if state == 'sent':
        form = f'<details class="access-retry"><summary>Изменить данные или запросить письмо ещё раз</summary>{form}</details>'
    if kind == 'reset-success':
        action = f'<a class="btn access-primary-link" href="{escape(login_url, quote=True)}">Перейти ко входу</a>'
    elif state in ('confirmed', 'pending-confirmation'):
        action = '<a class="btn access-primary-link" href="/profile/email">Вернуться к почте аккаунта</a>'
    elif state == 'invalid':
        action = ('<a class="btn access-primary-link" href="/forgot-password">Запросить новую ссылку</a>' if kind == 'reset' else
                  '<a class="btn access-primary-link" href="/profile/email">Запросить письмо в профиле</a>')
    elif state in ('unavailable', 'throttled'):
        action = '<a class="btn secondary access-primary-link" href="/login">Я помню пароль — войти</a>'
    else:
        action = ''
    explanation = {
        'forgot': ('Проверьте папку «Спам». Для восстановления нужны логин и ранее подтверждённая почта. '
                   'Ссылка действует 20 минут. Если вы ещё вошли на другом устройстве, почту можно проверить в профиле.'),
        'reset': 'От 8 до 256 символов. После смены пароля прежние сеансы завершатся. Проекты, работы и оценки сохранятся.',
        'reset-success': 'Проекты, работы и оценки сохранены. На других устройствах также войдите заново.',
        'confirm': 'Открытие ссылки само по себе ничего не меняет. При смене адреса нужны подтверждения обоих ящиков.',
    }[kind]
    retry = '<a class="action-link" href="/forgot-password">Запросить новую ссылку</a>' if kind == 'reset' and state != 'invalid' else ''
    return f'''<script src="/static/account-mail.js?v={ACCESS_VERSION}" defer></script><div class="auth-page auth-recovery access-page" data-access-state="{escape(state, quote=True)}">{access_header(login_url)}<div class="auth-card auth-recovery-card"><main class="auth-form-side"><div class="auth-form-inner">
        <h1>{escape(heading)}</h1><p class="auth-intro">{escape(intro)}</p><div class="auth-access-content">{feedback}{action}{form}</div>
        <details class="access-help"><summary>Что важно знать</summary><p>{explanation}</p></details>{retry}
        <nav class="auth-recovery-links" aria-label="Вернуться ко входу"><a href="/login">Вход ученика</a><a href="/teacher/login">Вход учителя</a></nav>
        </div></main></div><footer class="auth-page-footer"><a href="/data">Данные и помощь</a></footer></div>'''


def email_status(email, ready=True):
    if not ready:
        return dict(label='Отправка недоступна', tone='revision', title='Письма сейчас недоступны',
                    text='Подтверждённый адрес сохранён, но отправить письмо сейчас не получится.' if email['email'] else 'Почту можно подключить, когда отправка писем станет доступна.', action='Почта и восстановление')
    if email['pending_email']:
        return dict(label='Ждёт подтверждения', tone='review', title='Завершите подтверждение почты',
                    text='При смене нужны подтверждения старого и нового ящиков. Прежний адрес пока действует.' if email['email'] else 'Откройте письмо и подтвердите адрес. До этого восстановление по нему недоступно.', action='Проверить подтверждение')
    if email['email']:
        return dict(label='Подтверждена', tone='accepted', title='Восстановление настроено',
                    text='Если забудете пароль, ссылка придёт на подтверждённую почту.', action='Настройки почты')
    return dict(label='Не добавлена', tone='work', title='Добавьте почту для восстановления',
                text='Без заранее подтверждённого адреса восстановить забытый пароль по email не получится.', action='Добавить почту')


def email_panel(email, token, error='', notice='', ready=True, submitted=None):
    status = email_status(email, ready)
    current = (f'<p>Подтверждённый адрес: <strong>{escape(email["email"])}</strong></p>' if email['email'] else
               '<p>Почта ещё не подтверждена. Без неё восстановить пароль по email не получится.</p>')
    if email['pending_email']:
        pending_note = ('Старый подтверждённый адрес действует до завершения смены.' if email['email'] else
                        'Откройте письмо и подтвердите адрес, чтобы включить восстановление доступа.')
        current += f'<p>Ждёт подтверждения: <strong>{escape(email["pending_email"])}</strong>. {pending_note}</p>'
    checks = ''
    if email['pending_email']:
        rows = [(email['email'], email['old_approved'])] if email['email'] else []
        rows.append((email['pending_email'], email['new_approved']))
        checks = '<ul class="access-mail-checks" aria-label="Подтверждения адресов">' + ''.join(
            f'<li><span>{escape(address)}</span>{state_badge("Подтверждено" if approved else "Ждёт подтверждения", "accepted" if approved else "review")}</li>' for address, approved in rows) + '</ul>'
        checks += '<a class="btn secondary" href="/profile/email">Я подтвердил — проверить статус</a>'
    feedback = f'<div class="error auth-error" role="alert" tabindex="-1" data-account-email-error>{escape(error)}</div>' if error else ''
    if notice:
        feedback += f'<div class="ui-notice access-feedback" role="status">{escape(notice)}</div>'
    form = f'''<form method="post" class="team-profile-form account-email-form access-form"><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">
        <div class="access-field"><label for="recovery-email">Почта для восстановления</label><input id="recovery-email" name="email" type="email" autocomplete="email" maxlength="254" required value="{escape(email['pending_email'] if submitted is None else submitted, quote=True)}"></div>
        {password_control('email-password', 'password', 'Текущий пароль', 'current-password', ' maxlength="256"')}
        <button type="submit" class="auth-submit" data-pending-label="Отправляем…">Отправить подтверждение</button></form><p class="auth-submit-status" role="status" aria-live="polite"></p>''' if ready else '<p class="ui-notice" data-ui-tone="warning" role="status">Отправка почты пока не подключена. Попробуйте позже; вход и смена пароля по текущему паролю доступны.</p>'
    summary = 'Отправить письмо ещё раз или указать другой адрес' if email['pending_email'] else 'Изменить почту'
    if ready and (email['email'] or email['pending_email']):
        form = f'<details class="access-email-edit" {"open" if error else ""}><summary>{summary}</summary>{form}</details>'
    help_text = ('Для смены адреса подтвердите письма из прежнего и нового ящиков. Пока смена не завершена, действует прежний адрес.' if email['email'] else
                 'Неподтверждённый адрес не подходит для сброса пароля. Добавьте почту и подтвердите её из письма.')
    return f'''{account_assets()}<div class="account-access-layout account-email-layout access-email-page"><section class="account-email-panel" data-account-form>
        <header class="access-section-heading">{state_badge(status['label'], status['tone'])}<h2>{status['title']}</h2></header>
        <div class="account-email-state">{current}</div>{checks}{f'<p class="account-email-help">{help_text}</p>' if not email['pending_email'] else ''}{feedback}{form}</section></div>'''


def profile_panel(user, projects_count, email, token, message='', error='', ready=True):
    role = 'Учитель' if user['role'] == 'teacher' else 'Ученик'
    home = '/teacher' if user['role'] == 'teacher' else '/'
    mail = email_status(email, ready)
    skills = '<a class="action-link" href="/skills">Мой класс, направление и навыки</a>' if user['role'] == 'student' else ''
    feedback = f'<div class="success" role="status">{escape(message)}</div>' if message else ''
    error_html = f'<div class="error" role="alert" tabindex="-1" data-account-password-error>{escape(error)}</div>' if error else ''
    return f'''<main class="profile-card account-profile access-profile" data-access-page="profile">
        <a class="back" href="{home}">На главную</a><header class="access-profile-heading"><h1>Профиль</h1><p>Ваш аккаунт, почта и доступ к M-Flow.</p></header>{feedback}
        <div class="access-profile-grid"><section class="access-identity ui-surface" aria-labelledby="account-identity-title"><p class="eyebrow">ЛИЧНЫЙ АККАУНТ</p><h2 id="account-identity-title">{escape(user['username'])}</h2>
        <dl class="account-profile-facts"><dt>Роль</dt><dd>{role}</dd><dt>Проектов</dt><dd>{projects_count}</dd></dl>
        <nav class="access-account-links" aria-label="Разделы аккаунта">{skills}<a class="action-link" href="/settings">Настройки оформления и анимаций</a></nav></section>
        <section class="access-recovery-summary ui-surface" aria-labelledby="account-recovery-title"><p class="eyebrow">ПОЧТА И ВОССТАНОВЛЕНИЕ</p>{state_badge(mail['label'], mail['tone'])}<h2 id="account-recovery-title">{mail['title']}</h2><p>{mail['text']}</p>
        {f'<p class="access-current-email">{escape(email["email"])}</p>' if email['email'] else ''}<a class="btn {"secondary" if email['email'] and not email['pending_email'] else ""}" href="/profile/email">{mail['action']}</a></section></div>
        <details class="account-access-layout account-password-layout access-password-section" {"open" if error else ""}><summary><span><strong>Смена пароля</strong><small>Измените пароль, если хотите обновить доступ.</small></span></summary>
        <section class="account-security-panel" data-account-form><p class="account-section-intro">Текущий сеанс сохранится. После смены пароля другие сеансы этого аккаунта завершатся.</p>{error_html}
        <form method="post" class="access-form"><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">
        {password_control('old-password', 'old_password', 'Старый пароль', 'current-password')}
        {password_control('new-password', 'new_password', 'Новый пароль', 'new-password', ' minlength="8" maxlength="256"', 'От 8 до 256 символов')}
        {password_control('repeat-password', 'repeat_password', 'Повторите новый пароль', 'new-password', ' minlength="8" maxlength="256"')}
        <button type="submit" class="auth-submit" data-ui-icon="settings" data-pending-label="Сохраняем…">Сменить пароль</button></form><p class="auth-submit-status" role="status" aria-live="polite"></p></section></details></main>'''
