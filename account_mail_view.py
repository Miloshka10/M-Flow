"""Small server forms using the shared public-entry and account styles."""
from html import escape


def token_field(value=''):
    return f'''<label data-token-label for="mail-token">Код из письма</label><input id="mail-token" name="token" type="password" autocomplete="off" minlength="43" maxlength="43" value="{escape(value, quote=True)}" required>'''


def public_form(title, intro, fields, token, button, error='', notice='', token_form=False, ready=True):
    feedback = f'<div class="error auth-error" role="alert" tabindex="-1">{escape(error)}</div>' if error else ''
    if notice:
        feedback += f'<p class="ok" role="status">{escape(notice)}</p>'
    form = (f'<form method="post" {("data-email-token-form" if token_form else "")}><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">{fields}<button type="submit" class="auth-submit" data-pending-label="Сохраняем…">{escape(button)}</button></form><p class="auth-submit-status" role="status" aria-live="polite"></p>' if ready else '')
    return f'''<script src="/static/account-mail.js?v=20261031" defer></script><div class="auth-page auth-recovery"><div class="auth-card auth-recovery-card"><main class="auth-form-side"><div class="auth-form-inner">
        <a class="brand auth-form-brand" href="/login">M-Flow<span class="ui-brand-dot">.</span></a><h1>{escape(title)}</h1><p class="auth-intro">{escape(intro)}</p>{feedback}{form}
        <p class="auth-footer"><a href="/login">Вход ученика</a> · <a href="/teacher/login">Вход учителя</a></p><p class="auth-footer"><a href="/data">Данные и помощь</a></p>
        </div></main></div></div>'''


def email_panel(email, token, error='', notice='', ready=True, submitted=None):
    current = (f'<p>Подтверждённый адрес: <strong>{escape(email["email"])}</strong></p>' if email['email'] else
               '<p>Почта ещё не подтверждена. Без неё восстановить пароль по email не получится.</p>')
    if email['pending_email']:
        pending_note = ('Старый подтверждённый адрес действует до завершения смены.' if email['email'] else
                        'Откройте письмо и подтвердите адрес, чтобы включить восстановление доступа.')
        current += f'<p>Ждёт подтверждения: {escape(email["pending_email"])}. {pending_note}</p>'
    feedback = f'<p class="error" role="alert">{escape(error)}</p>' if error else ''
    if notice:
        feedback += f'<p class="ok" role="status">{escape(notice)}</p>'
    form = f'''<form method="post" class="team-profile-form"><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">
        <label for="recovery-email">Почта для восстановления</label><input id="recovery-email" name="email" type="email" autocomplete="email" maxlength="254" required value="{escape(email['pending_email'] if submitted is None else submitted, quote=True)}">
        <label for="email-password">Текущий пароль</label><input id="email-password" name="password" type="password" autocomplete="current-password" maxlength="256" required>
        <button type="submit">Отправить подтверждение</button></form>''' if ready else '<p>Отправка почты пока не подключена. Попробуйте позже; вход и смена пароля по текущему паролю доступны.</p>'
    return f'<section class="card">{current}<p>При добавлении подтвердите новый ящик. Для смены уже подтверждённого адреса нужны оба ящика — старый и новый. Неподтверждённый адрес не подходит для сброса пароля.</p>{feedback}{form}</section>'
