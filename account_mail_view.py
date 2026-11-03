"""Presentation for account access; handlers and server checks remain authoritative."""
from html import escape
import re

ACCESS_VERSION = '20261102-layout-review'


def password_control(input_id, name, label, autocomplete, limits='', hint=''):
    subject = 'повтор пароля' if name == 'repeat_password' else 'текущий пароль' if name in ('old_password',) else 'пароль'
    described = f' aria-describedby="{input_id}-hint"' if hint else ''
    note = f'<p class="auth-hint" id="{input_id}-hint">{escape(hint)}</p>' if hint else ''
    return f'''<label for="{input_id}">{escape(label)}</label><div class="auth-password-control">
        <input id="{input_id}" name="{name}" type="password" autocomplete="{autocomplete}"{limits}{described} required>
        <button class="auth-password-toggle" type="button" aria-controls="{input_id}" aria-pressed="false" aria-label="Показать {subject}" hidden>Показать</button></div>{note}'''


def account_assets():
    return f'<link rel="stylesheet" href="/static/auth.css?v={ACCESS_VERSION}"><script src="/static/auth.js?v={ACCESS_VERSION}" defer></script><script src="/static/account-mail.js?v=20261101-profile-feedback" defer></script>'


def access_aside(kind):
    content = {
        'login': ('Ваше пространство для работы', 'Войдите в свой аккаунт, чтобы продолжить проекты и вернуться к обратной связи.', 'Всё остаётся в аккаунте', 'Проекты, работы и решения учителя сохраняются между входами.', 'Забыли пароль?', 'Восстановление доступно через почту, заранее подтверждённую в профиле.'),
        'register': ('Начните со своего аккаунта', 'Выберите роль и создайте личный аккаунт M-Flow.', 'Почта — следующий шаг', 'После входа добавьте и подтвердите адрес в профиле. Он поможет восстановить доступ.', 'Учителю нужен код', 'Код регистрации организатор передаёт лично. Код группы или проекта для этого не подходит.'),
        'forgot': ('Письмо, которое вернёт доступ', 'Используйте логин и адрес, который вы заранее подтвердили в профиле.', 'Ссылка действует 20 минут', 'Она поможет выбрать новый пароль и используется один раз.', 'Не помните подтверждённую почту?', 'Если вы ещё вошли на другом устройстве, откройте профиль. Иначе обратитесь к организатору.'),
        'reset': ('Пароль для следующего входа', 'После сохранения войдите с новым паролем. Ваши проекты и результаты сохранятся.', 'От 8 до 256 символов', 'Повторите пароль в обоих полях. Кнопка «Показать» поможет проверить ввод.', 'Прежние сеансы завершатся', 'На других устройствах потребуется войти с новым паролем.'),
        'reset-success': ('Можно вернуться к работе', 'Используйте новый пароль на обычном экране входа.', 'Проекты и результаты сохранены', 'Сброс пароля не меняет работы, замечания и оценки.', 'Прежние сеансы завершены', 'На других устройствах также войдите заново.'),
        'confirm': ('Почта для вашего аккаунта', 'Подтвердите действие отдельным нажатием. Открытие ссылки само по себе ничего не меняет.', 'Ссылка действует 24 часа', 'Используйте письмо, которое получили после запроса в профиле.', 'При смене нужны оба ящика', 'Пока оба подтверждения не получены, действует прежний адрес.'),
        'email': ('Личная почта для доступа', 'Подтвердите адрес, чтобы вернуть доступ к аккаунту, если забудете пароль.', 'Подтверждение — из письма', 'Неподтверждённый адрес не подходит для восстановления.', 'Ваши данные — в профиле', 'Для добавления или смены адреса нужен текущий пароль.'),
        'email-change': ('Подтвердите оба адреса', 'Для смены почты нужны подтверждения прежнего и нового ящиков.', 'Порядок не важен', 'Можно начать с любого письма. Смена завершится только после двух подтверждений.', 'Старый адрес пока действует', 'До завершения смены через него можно восстановить доступ.'),
        'password': ('Обновите доступ к аккаунту', 'Текущий пароль подтверждает, что изменение запрашиваете вы.', 'Текущий сеанс сохранится', 'Другие сеансы этого аккаунта после смены будут завершены.', 'Проекты и оценки сохранятся', 'Смена пароля сохраняет проекты, историю и оценки.'),
    }
    title, intro, heading, detail, heading2, detail2 = content[kind]
    notes = f'<div class="auth-access-note"><h3>{heading}</h3><p>{detail}</p></div><div class="auth-access-extra"><h3>{heading2}</h3><p>{detail2}</p></div>'
    intro_html = f'<p class="auth-eyebrow">ПОЧТА И ДОСТУП</p><h2>{title}</h2><p class="auth-access-copy">{intro}</p>'
    if kind in ('login', 'register'):
        return f'<aside class="auth-access-aside" aria-label="Полезно знать"><details class="auth-access-guide"><summary>Как устроен доступ</summary><div class="auth-access-guide-content">{intro_html}{notes}</div></details></aside>'
    return f'<aside class="auth-access-aside" aria-label="Полезно знать">{intro_html}{notes}</aside>'


def token_field(value=''):
    return f'''<label data-token-label for="mail-token">Код из письма</label><input id="mail-token" name="token" type="password" autocomplete="off" minlength="43" maxlength="43" value="{escape(value, quote=True)}" required>'''


def _password_controls(fields):
    # Only ordinary password fields receive visibility controls, never the email capability.
    def wrap(match):
        tag, input_id = match.group(0), match.group(1)
        if input_id == 'mail-token' or not re.search(r'name="(?:password|repeat_password)"', tag):
            return tag
        return f'<div class="auth-password-control">{tag}<button class="auth-password-toggle" type="button" aria-controls="{input_id}" aria-pressed="false" aria-label="Показать пароль" hidden>Показать</button></div>'
    return re.sub(r'<input id="([^"]+)"[^>]*type="password"[^>]*>', wrap, fields)


def public_form(title, intro, fields, token, button, error='', notice='', token_form=False, ready=True):
    kind = 'reset' if title == 'Новый пароль' else 'reset-success' if title == 'Пароль изменён' else 'confirm' if title == 'Подтверждение почты' else 'forgot'
    feedback = f'<div class="error auth-error" role="alert" tabindex="-1">{escape(error)}</div>' if error else ''
    if notice:
        feedback += f'<p class="ok auth-access-status" role="status">{escape(notice)}</p>'
    fields = _password_controls(fields)
    detail = {
        'forgot': 'Нужна почта, которую вы заранее подтвердили в профиле. Ссылка восстановления действует 20 минут.',
        'reset': 'От 8 до 256 символов. После смены пароля прежние сеансы завершатся, проекты и оценки сохранятся.',
        'reset-success': 'На других устройствах также потребуется войти с новым паролем.',
        'confirm': 'Открытие ссылки само по себе ничего не меняет. При смене почты нужны подтверждения обоих ящиков.',
    }[kind]
    retry_link = '<p class="auth-retry"><a href="/forgot-password">Запросить новую ссылку</a></p>' if kind == 'reset' else ''
    form = (f'<form method="post" {("data-email-token-form" if token_form else "")}><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">{fields}<button type="submit" class="auth-submit" data-pending-label="Сохраняем…">{escape(button)}</button></form><p class="auth-submit-status" role="status" aria-live="polite"></p>' if ready else '')
    return f'''<script src="/static/account-mail.js?v=20261101-profile-feedback" defer></script><div class="auth-page auth-recovery"><div class="auth-card auth-recovery-card"><main class="auth-form-side"><div class="auth-form-inner">
        <a class="brand auth-form-brand" href="/login">M-Flow<span class="ui-brand-dot">.</span></a><h1>{escape(title)}</h1><p class="auth-intro">{escape(intro)}</p><div class="auth-access-content">{feedback}{form}</div>
        <p class="auth-recovery-note">{detail}</p>{retry_link}
        <nav class="auth-recovery-links" aria-label="Вернуться ко входу"><a href="/login">Вход ученика</a><a href="/teacher/login">Вход учителя</a></nav><p class="auth-recovery-data"><a href="/data">Данные и помощь</a></p>
        </div></main></div></div>'''


def email_panel(email, token, error='', notice='', ready=True, submitted=None):
    current = (f'<p>Подтверждённый адрес: <strong>{escape(email["email"])}</strong></p>' if email['email'] else
               '<p>Почта ещё не подтверждена. Без неё восстановить пароль по email не получится.</p>')
    if email['pending_email']:
        pending_note = ('Старый подтверждённый адрес действует до завершения смены.' if email['email'] else
                        'Откройте письмо и подтвердите адрес, чтобы включить восстановление доступа.')
        current += f'<p>Ждёт подтверждения: <strong>{escape(email["pending_email"])}</strong>. {pending_note}</p>'
    feedback = f'<p class="error auth-error" role="alert" tabindex="-1" data-account-email-error>{escape(error)}</p>' if error else ''
    if notice:
        feedback += f'<p class="ok auth-access-status" role="status">{escape(notice)}</p>'
    form = f'''<form method="post" class="team-profile-form account-email-form"><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">
        <label for="recovery-email">Почта для восстановления</label><input id="recovery-email" name="email" type="email" autocomplete="email" maxlength="254" required value="{escape(email['pending_email'] if submitted is None else submitted, quote=True)}">
        {password_control('email-password', 'password', 'Текущий пароль', 'current-password', ' maxlength="256"')}
        <button type="submit" data-pending-label="Отправляем…">Отправить подтверждение</button></form><p class="auth-submit-status" role="status" aria-live="polite"></p>''' if ready else '<p class="auth-access-status" role="status">Отправка почты пока не подключена. Попробуйте позже; вход и смена пароля по текущему паролю доступны.</p>'
    help_text = ('Для смены адреса подтвердите письма из прежнего и нового ящиков. Пока смена не завершена, действует прежний адрес.' if email['email'] else
                 'Неподтверждённый адрес не подходит для сброса пароля. Добавьте почту и подтвердите её из письма.')
    return f'{account_assets()}<div class="account-access-layout account-email-layout"><section class="card account-email-panel" data-account-form><div class="account-email-state">{current}</div><p class="account-email-help">{help_text}</p>{feedback}{form}</section></div>'
