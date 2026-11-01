"""Disposable loopback mail QA. Default: capture only. --emailjs: explicit opt-in.

Never loads a .env or an existing database. Real-provider credentials are entered
without echo, kept in memory, and never served to the browser. This launcher is
not imported by the application and cannot enable HTTP mail links in production.
"""
import argparse
from contextlib import contextmanager, closing
from dataclasses import replace
import getpass
from html import escape
import importlib.util
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import warnings
from unittest.mock import patch

from account_mail import MailSettings, MailDispatcher, normalize_email, send_mail, emailjs_gate, MailDeliveryError
from flask import abort, request
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server, WSGIRequestHandler


MAX_LETTERS = 12


class PreviewSetupError(ValueError):
    """Only fixed, safe setup guidance; never include an entered value."""


def hidden(prompt):
    if not sys.stdin.isatty():
        raise PreviewSetupError('Скрытый ввод недоступен. Запустите команду в обычном окне PowerShell.')
    # Never accept getpass's fallback that would echo a credential.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, EOFError):
            raise PreviewSetupError('Скрытый ввод прерван или недоступен. Запустите команду в обычном окне PowerShell.') from None


def validated_input(label, valid, guidance, optional=False):
    while True:
        value = hidden(label + ': ').strip()
        if optional and not value:
            return ''
        try:
            accepted = valid(value)
        except ValueError:
            accepted = False
        if accepted:
            return value
        print(f'Не принято поле «{label}». {guidance} Значение не выводится; повторите только это поле.')


def mail_settings(values):
    with patch.dict(os.environ, values):
        return MailSettings.from_environment()


def provider_settings():
    print('Настройки вводятся без отображения и не сохраняются в файлы.')
    # Validate each field with the same production parser. Valid placeholders
    # let us identify the failing field without printing any private values.
    values = dict(M_FLOW_MAIL_PROVIDER='emailjs', M_FLOW_PUBLIC_URL='https://local-preview.invalid',
                  M_FLOW_MAIL_FROM='sender@example.org', M_FLOW_MAIL_SERVICE_ID='service_preview',
                  M_FLOW_MAIL_TEMPLATE_ID='template_preview', M_FLOW_MAIL_PUBLIC_KEY='preview-public',
                  M_FLOW_MAIL_API_KEY='preview-private')
    key_help = 'Скопируйте только значение ключа, без подписи, кавычек и внутренних пробелов.'
    id_help = 'Скопируйте только ID; допустимы латинские буквы, цифры, дефис и подчёркивание.'
    for name, label, guidance in (
        ('M_FLOW_MAIL_FROM', 'Подключённый адрес Gmail', 'Нужен полный адрес почты, без имени отправителя.'),
        ('M_FLOW_MAIL_SERVICE_ID', 'Service ID', id_help),
        ('M_FLOW_MAIL_TEMPLATE_ID', 'Template ID', id_help),
        ('M_FLOW_MAIL_PUBLIC_KEY', 'Public Key', key_help),
        ('M_FLOW_MAIL_API_KEY', 'Private Key', key_help),
    ):
        values[name] = validated_input(label, lambda value: mail_settings({**values, name: value}) is not None,
                                       guidance)
    settings = mail_settings(values)
    if settings is None:
        raise PreviewSetupError('Настройки EmailJS не прошли проверку формата; отправка не включена.')
    print('Формат настроек принят. Действительность ключей и доставку ещё нужно проверить отправкой.')
    email_help = 'Нужен полный адрес вашего тестового ящика.'
    first = validated_input('Ваш тестовый адрес получателя', normalize_email, email_help)
    recipients = {normalize_email(first)}
    second = validated_input('Второй ваш ящик для смены почты (Enter — пропустить)', normalize_email,
                             email_help, optional=True)
    if second:
        recipients.add(normalize_email(second))
    print('Будут разрешены письма только выбранным ящикам, максимум 12 за запуск.')
    print('Подтвердите: шаблон сохранён, история выключена, ящики принадлежат вам.')
    if input('Для разрешения отправки введите ОТПРАВКА: ').strip() != 'ОТПРАВКА':
        raise PreviewSetupError('Настоящая отправка не разрешена. Для повторной попытки запустите команду заново.')
    return settings, recipients


class PreviewDelivery:
    def __init__(self, recipients, real=False, sender=send_mail):
        self.recipients = frozenset(normalize_email(value) for value in recipients)
        self.real, self.sender = real, sender
        self._lock = threading.Lock()
        self._attempts = 0
        self._letters = []

    def __call__(self, settings, recipient, subject, body):
        recipient = normalize_email(recipient)
        with self._lock:
            if recipient not in self.recipients:
                raise MailDeliveryError('PREVIEW_RECIPIENT')
            if self._attempts >= MAX_LETTERS:
                raise MailDeliveryError('PREVIEW_LIMIT')
            self._attempts += 1  # Count failed attempts too; no hidden retries.
            if not self.real:
                self._letters.append((recipient, subject, body))
        if self.real:
            self.sender(settings, recipient, subject, body)
            print('Провайдер принял тестовое письмо. Получение проверьте в ящике.')

    def captured(self):
        with self._lock:
            return list(reversed(self._letters))


def report_delivery_failure(code):
    hints = {
        'PREVIEW_RECIPIENT': 'Адрес в форме не совпадает с разрешённым тестовым ящиком.',
        'PREVIEW_LIMIT': 'Достигнут предел 12 попыток за этот запуск.',
        'EMAILJS_RECIPIENT': 'Адрес получателя не прошёл проверку формата.',
        'EMAILJS_HTTP_400': 'EmailJS отклонил параметры. Проверьте Service ID, Template ID и настройки шаблона.',
        'EMAILJS_HTTP_401': 'EmailJS отклонил авторизацию. Проверьте Public Key и Private Key.',
        'EMAILJS_HTTP_403': 'EmailJS запретил запрос. Проверьте ключи и разрешение серверных запросов в Account → Security.',
        'EMAILJS_HTTP_404': 'Ресурс EmailJS не найден. Проверьте Service ID и Template ID.',
        'EMAILJS_HTTP_422': 'EmailJS не принял данные письма. Проверьте To Email = {{to_email}} и подключение сервиса.',
        'EMAILJS_HTTP_429': 'EmailJS ограничил запросы. Проверьте квоту; не повторяйте отправку подряд.',
        'EMAILJS_NON_BROWSER': 'EmailJS сообщает: серверные API-запросы отключены для аккаунта, которому принадлежат введённые ключи.',
        'EMAILJS_PUBLIC_KEY': 'EmailJS сообщает: Public Key недействителен. Сверьте его с Account своего аккаунта EmailJS.',
        'EMAILJS_PRIVATE_KEY': 'EmailJS сообщает: Private Key / access token недействителен. Сверьте ключи одного аккаунта EmailJS.',
        'EMAILJS_ORIGIN': 'Ответ EmailJS сообщает об ограничении origin. Это требует отдельной проверки настроек доступа.',
        'EMAILJS_HTML_REFUSAL': 'Вместо обычного ответа API получена HTML-страница отказа. Возможна блокировка сервисом или сетью.',
        'EMAILJS_TLS': 'Не удалось проверить защищённое соединение с EmailJS. Проверка сертификатов остаётся включённой.',
        'EMAILJS_TIMEOUT': 'Истекло время ожидания ответа EmailJS.',
        'EMAILJS_NETWORK': 'Не удалось установить соединение с EmailJS.',
        'EMAILJS_RECEIPT': 'EmailJS не вернул ожидаемое подтверждение OK.',
    }
    # Never print an unrecognized argument, exception, recipient or provider response.
    if isinstance(code, str) and code in hints:
        print(f'Тестовое письмо не отправлено [{code}]. {hints[code]}')
    elif isinstance(code, str) and re.fullmatch(r'EMAILJS_HTTP_[1-5][0-9]{2}', code):
        print(f'Тестовое письмо не отправлено [{code}]. EmailJS вернул отказ; проверьте состояние Email Services.')
    else:
        print('Тестовое письмо не отправлено [MAIL_JOB_FAILED]. Сбой локального почтового задания или ожидания отправки.')


class QuietRequests(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass  # Do not log URLs, login names or token parameters.


@contextmanager
def preview(settings=None, recipients=('first@example.org', 'second@example.org')):
    """Caller serves/stops the bound server; all state is disposed on context exit."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('M_FLOW_') and key != 'DATABASE_URL'}
    with tempfile.TemporaryDirectory(prefix='mflow-mail-preview-') as directory:
        environment.update(M_FLOW_DEBUG='0', M_FLOW_SECRET_KEY=secrets.token_urlsafe(48),
                           M_FLOW_TEACHER_CODE=secrets.token_urlsafe(32),
                           M_FLOW_DATABASE_BACKEND='sqlite',
                           M_FLOW_DATABASE=str(Path(directory) / 'preview.sqlite3'))
        with patch.dict(os.environ, environment, clear=True):
            spec = importlib.util.spec_from_file_location('mflow_local_mail_preview', Path(__file__).with_name('app.py'))
            module = importlib.util.module_from_spec(spec)
            # Flask resolves static assets through the module location, even
            # when this launcher is invoked from a different working directory.
            with patch.dict(sys.modules, {spec.name: module}):
                spec.loader.exec_module(module)
            module.app.config['MAIL_DISPATCHER']._pool.shutdown(wait=True)
            password = secrets.token_urlsafe(12)
            with closing(module.get_db()) as conn:
                for name, role in (('mail-student', 'student'), ('mail-teacher', 'teacher')):
                    conn.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',
                                 (name, generate_password_hash(password), role))
                conn.commit()
            delivery = PreviewDelivery(recipients, real=settings is not None)
            dispatcher = MailDispatcher(
                lambda: print('Тестовое письмо не отправлено. Проверьте настройки, получателя и лимит.'),
                sender=delivery,
                gate=lambda config: emailjs_gate(module.get_db, module.SECRET_KEY, config.public_key),
                diagnostic=report_delivery_failure)
            server = None
            try:
                server = make_server('127.0.0.1', 0, module.app, threaded=True, request_handler=QuietRequests)
                origin = f'http://127.0.0.1:{server.server_port}'
                config = settings or MailSettings('', 0, '', '', 'sender@example.org', origin, '', 'emailjs',
                                                  'capture-only', 'capture-only', 'service_capture', 'template_capture')
                # Only this disposable launcher permits loopback HTTP links.
                module.app.config.update(MAIL_SETTINGS=replace(config, origin=origin),
                                         PUBLIC_ORIGIN='', SESSION_COOKIE_SECURE=False,
                                         MAIL_DISPATCHER=dispatcher)

                @module.app.before_request
                def loopback_only():
                    if request.remote_addr != '127.0.0.1' or request.host != origin.removeprefix('http://'):
                        abort(403)

                if settings is None:
                    @module.app.get('/__qa_mailbox')
                    def mailbox():
                        body = ('<main class="container"><h1>Тестовые письма</h1>'
                                '<p>Письма перехвачены. Интернет и рабочая база не используются.</p>'
                                '<a href="/login">Вход ученика</a> · <a href="/teacher/login">Вход учителя</a>')
                        for recipient, subject, message in delivery.captured():
                            link = re.search(re.escape(origin) + r'/[^\s]+#token=[A-Za-z0-9_-]{43}', message)
                            action = f'<p><a href="{escape(link[0], quote=True)}">Открыть тестовую ссылку</a></p>' if link else ''
                            body += (f'<section class="card"><h2>{escape(subject)}</h2><p>{escape(recipient)}</p>'
                                     f'<pre style="white-space:pre-wrap;overflow-wrap:anywhere">{escape(message)}</pre>{action}</section>')
                        return module.render_page(body + '</main>', title='Только локальная проверка')

                yield module, server, password, delivery
            finally:
                if server is not None:
                    server.server_close()
                dispatcher._pool.shutdown(wait=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emailjs', action='store_true', help='Разрешить настоящие письма после закрытого ввода настроек.')
    args = parser.parse_args()
    settings, recipients = provider_settings() if args.emailjs else (None, ('first@example.org', 'second@example.org'))
    with preview(settings, recipients) as (_, server, password, __):
        print(f'Только локальная тестовая база: http://127.0.0.1:{server.server_port}/login')
        print('Тестовые логины: mail-student и mail-teacher. Вход учителя: /teacher/login')
        print('Одноразовый пароль этих вымышленных аккаунтов:', password)
        if settings is None:
            print('Режим перехвата: используйте first@example.org / second@example.org, письма — /__qa_mailbox.')
        else:
            print('Ссылки из писем открываются только на этом компьютере, пока работает запуск.')
        print('Остановка: Ctrl+C. После остановки временная база и ссылки удаляются.')
        server.serve_forever()


def run():
    try:
        main()
    except KeyboardInterrupt:
        print('\nТестовый запуск остановлен.')
        return 0
    except PreviewSetupError as error:
        print(str(error))  # This exception is only constructed with fixed guidance.
        return 1
    except Exception:
        print('Не удалось запустить локальный стенд (LOCAL_START). Значения настроек скрыты; рабочая база не открывалась.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
