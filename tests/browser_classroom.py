"""Browser smoke for the two classroom roles, on a synthetic loopback SQLite."""
from contextlib import closing, contextmanager
from datetime import date, timedelta
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@contextmanager
def classroom_server():
    with tempfile.TemporaryDirectory(prefix='mflow-browser-') as directory, patch.dict(os.environ, {
        'M_FLOW_DATABASE_BACKEND': 'sqlite',
        'M_FLOW_DATABASE': str(Path(directory) / 'browser.db'),
        'M_FLOW_SECRET_KEY': 'local-browser-smoke-only',
        'M_FLOW_DEBUG': '0',
        'M_FLOW_TEACHER_USER': '',
        'M_FLOW_TEACHER_PASSWORD': '',
    }):
        import app
        app.app.config.update(SESSION_COOKIE_SECURE=False)
        with closing(app.get_db()) as conn:
            password = generate_password_hash('browser-smoke-password')
            conn.executemany('INSERT INTO users(username,password,role) VALUES(?,?,?)', [
                ('teacher-smoke', password, 'teacher'), ('student-smoke', password, 'student'),
                ('outside-smoke', password, 'teacher'),
            ])
            conn.commit()
        server = make_server('127.0.0.1', 0, app.app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


def login(page, url, username):
    page.goto(url)
    page.get_by_label('Логин', exact=True).fill(username)
    page.get_by_label('Пароль', exact=True).fill('browser-smoke-password')
    page.get_by_role('button', name='Войти', exact=True).click()


def submit_stage(page, result, presentation=False):
    page.locator('textarea[name="result"]').fill(result)
    if presentation:
        page.get_by_label('Ссылка на презентацию').fill('https://example.com/slides')
    page.get_by_role('button', name='Отправить учителю').click()
    expect(page.get_by_text('Этап отправлен учителю на проверку.', exact=True)).to_be_visible()


def exercise(browser, base):
    teacher = browser.new_page()
    student = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    outsider = browser.new_page()
    for page in (teacher, student, outsider):
        page.set_default_timeout(8000)
    login(teacher, base + '/teacher/login', 'teacher-smoke')
    expect(teacher.get_by_role('heading', name='Кабинет учителя')).to_be_visible()
    expect(teacher.locator('main a[href="/teacher/review"]:visible').first).to_be_visible()
    teacher.goto(base + '/groups')
    new_group = teacher.locator("details.ds-group-form")
    if not new_group.locator("input[name=name]").is_visible():
        new_group.locator("summary").click()
    teacher.get_by_label('Название группы').fill('10 А — браузерная проверка')
    teacher.get_by_role('button', name='Создать группу', exact=True).click()
    group_url = teacher.url
    teacher.get_by_role('button', name='Создать новый код группы').click()
    code = teacher.get_by_label('Код группы', exact=True).input_value()
    teacher.get_by_role('link', name='Выдать задание').click()
    teacher.get_by_label('Название задания').fill('Метеостанция')
    teacher.get_by_label('Цель', exact=True).fill('Сравнить показания двух датчиков')
    teacher.get_by_label('Срок сдачи').fill((date.today() + timedelta(days=14)).isoformat())
    teacher.get_by_label('Материалы и инструкции').fill('Три измерения на каждом датчике')
    teacher.get_by_role('button', name='Выдать задание').click()
    assignment_url = teacher.url
    expect(teacher.get_by_role('heading', name='Прогресс класса')).to_be_visible()

    login(student, base + '/login', 'student-smoke')
    student.goto(base + '/groups')
    join_group = student.locator("details#group-code")
    if not join_group.locator("input[name=code]").is_visible():
        join_group.locator("summary").click()
    student.get_by_label('Код группы от учителя').fill(code)
    student.get_by_role('button', name='Проверить код группы').click()
    student.get_by_role('button', name='Подтвердить вступление').click()
    expect(student).to_have_url(group_url)
    student.goto(base + '/')
    expect(student.get_by_role('heading', name='Задания групп')).to_be_visible()
    student.get_by_role('link', name='Все задания').click()
    expect(student.get_by_role('heading', name='Мои задания')).to_be_visible()
    student.get_by_role('link', name='Метеостанция').click()
    expect(student.get_by_text('Три измерения на каждом датчике')).to_be_visible()
    student.get_by_role('button', name='Создать проект по заданию').click()
    expect(student.get_by_text('Проект связан с заданием.')).to_be_visible()
    project_path = student.get_by_role('link', name='Обзор проекта', exact=True).get_attribute('href')
    assert teacher.goto(base + project_path).status == 403
    student.get_by_role('button', name='Предложить сопровождение учителю группы').click()

    teacher.goto(assignment_url)
    expect(teacher.get_by_role('link', name='Нужно согласовать сопровождение: 1')).to_be_visible()
    teacher.goto(base + '/supervision')
    teacher.get_by_role('button', name='Подтвердить', exact=True).click()
    student.reload()
    student.get_by_role('link', name='Подготовить результат').click()
    submit_stage(student, 'Первый результат замеров')

    teacher.goto(assignment_url)
    teacher.get_by_role('link', name='Ждёт проверки: 1').click()
    teacher.get_by_role('link', name='Открыть сдачу').click()
    expect(teacher.get_by_text('Первый результат замеров', exact=True)).to_be_visible()
    teacher.get_by_label('Комментарий ученикам').fill('Добавьте единицы измерения.')
    teacher.get_by_role('button', name='На доработку', exact=True).click()
    teacher.get_by_role('link', name='К заданию').click()
    assert urlsplit(teacher.url).query == 'view=review'
    expect(teacher.get_by_role('link', name='На доработке: 1')).to_be_visible()

    student.goto(assignment_url)
    student.get_by_role('link', name='Перейти к доработке').click()
    expect(student.get_by_text('Что исправить: Добавьте единицы измерения.')).to_be_visible()
    submit_stage(student, 'Средняя температура 20 градусов Цельсия')
    teacher.goto(assignment_url)
    teacher.get_by_role('link', name='Открыть сдачу').click()
    expect(teacher.get_by_text('Средняя температура 20 градусов Цельсия')).to_be_visible()
    teacher.get_by_role('button', name='Принять этап').click()
    expect(teacher.get_by_text('Этап принят. Следующий этап открыт.')).to_be_visible()

    for stage in range(2, 6):
        student.goto(assignment_url)
        student.get_by_role('link', name='Подготовить результат').click()
        submit_stage(student, f'Готовый результат этапа {stage}', presentation=stage == 5)
        teacher.goto(assignment_url)
        teacher.get_by_role('link', name='Открыть сдачу').click()
        expect(teacher.get_by_text(f'Готовый результат этапа {stage}', exact=True)).to_be_visible()
        teacher.get_by_role('button', name='Принять этап').click()

    student.goto(assignment_url)
    student.get_by_role('link', name='Подготовить видеозащиту').click()
    student.get_by_label('Ссылка на запись видео').fill('https://example.com/recording')
    student.get_by_label('Описание выступления').fill('Методика и результаты замеров')
    student.get_by_role('button', name='Отправить защиту учителю').click()
    expect(student.get_by_text('Запись ожидает проверки учителя.', exact=False)).to_be_visible()
    teacher.goto(assignment_url)
    teacher.get_by_role('link', name='Открыть сдачу').click()
    teacher.get_by_role('button', name='Принять видеозащиту').click()
    teacher.get_by_role('link', name='К заданию').click()
    expect(teacher.get_by_role('link', name='К оцениванию: 1')).to_be_visible()
    teacher.goto(group_url)
    teacher.get_by_role('link', name='Результаты по заданиям').click()
    teacher.get_by_role('link', name='Метеостанция').click()
    teacher.get_by_role('link', name='Выставить оценку').click()
    teacher.get_by_label('ФИО ученика').fill('Тестовый ученик')
    from grading import CRITERIA
    for key, _, maximum in CRITERIA:
        teacher.locator(f'select[name="criterion_{key}"]').select_option(str(maximum))
    teacher.get_by_role('button', name='Опубликовать оценку').click()
    expect(teacher.get_by_text('Оценка опубликована.', exact=True)).to_be_visible()
    teacher.get_by_role('link', name='К заданию').click()
    expect(teacher.get_by_role('link', name='Оценка опубликована: 1')).to_be_visible()
    expect(teacher.get_by_text('Опубликована · 45 / 45 · оценка 5')).to_be_visible()
    student.goto(assignment_url)
    student.get_by_role('link', name='Посмотреть свои оценки').click()
    expect(student.get_by_text('Итого: 45 / 45 · Оценка: 5')).to_be_visible()

    login(outsider, base + '/teacher/login', 'outside-smoke')
    for path in (urlsplit(assignment_url).path, project_path, project_path + '/history', project_path + '/assessment'):
        assert outsider.goto(base + path).status == 403, path
    print('PASS: class assignment, consent, five stages, return and resubmission, defense, grade and outsider isolation.')


if __name__ == '__main__':
    with classroom_server() as base, sync_playwright() as playwright:
        channel = os.environ.get('M_FLOW_BROWSER_CHANNEL')
        with playwright.chromium.launch(channel=channel) as browser:
            exercise(browser, base)
