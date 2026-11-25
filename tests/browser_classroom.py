"""Browser smoke for the two classroom roles, on a synthetic loopback SQLite."""
from contextlib import closing, contextmanager
from datetime import date, timedelta
import os
from pathlib import Path
import re
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


def rich_test_uploads():
    from test_rich_attachment_text import scanned_work, text_pdf, workbook

    return [
        {'name': 'report.pdf', 'mimeType': 'application/pdf',
         'buffer': text_pdf('Sensor A: 20.1, 20.2, 20.3 Celsius.')},
        {'name': 'measurements.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         'buffer': workbook('<row><c r="A1" t="inlineStr"><is><t>Датчик Б</t></is></c>'
                            '<c r="B1"><v>23.4</v></c><c r="C1"><v>24.7</v></c><c r="D1"><v>25.1</v></c></row>')},
        {'name': 'scan.png', 'mimeType': 'image/png', 'buffer': scanned_work('PNG')},
    ]


def exercise(browser, base):
    real_analysis = os.environ.get('M_FLOW_TEST_OLLAMA') == '1'
    criterion = 'В PDF приведены три показания датчика A'
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
    teacher.get_by_label('Цель', exact=True).fill('Проверьте три показания датчика A в report.pdf.'
                                               if real_analysis else 'Сравнить показания двух датчиков')
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
    teacher.goto(base + project_path)
    teacher.get_by_role('link', name='Задать критерии').click()
    teacher.get_by_label('Что должен подтвердить результат').fill(criterion)
    teacher.get_by_role('button', name='Добавить критерий').click()
    expect(teacher.get_by_text(criterion, exact=True)).to_be_visible()
    student.reload()
    student.get_by_role('link', name='Подготовить результат').click()
    student.get_by_label('Файлы для сдачи').set_input_files(
        {'name': 'measurements.txt', 'mimeType': 'text/plain', 'buffer': b'Measurement v1'})
    student.get_by_label('Пояснение', exact=True).fill('Пока есть один замер.')
    student.get_by_label('Файл для критерия').select_option(label='measurements.txt')
    submit_stage(student, 'Первый результат замеров')

    teacher.goto(assignment_url)
    teacher.get_by_role('link', name='Ждёт проверки: 1').click()
    teacher.get_by_role('link', name='Открыть сдачу').click()
    expect(teacher.get_by_text('Первый результат замеров', exact=True)).to_be_visible()
    with teacher.expect_download() as event:
        teacher.locator('.submission-attachments').get_by_role('link', name='measurements.txt').click()
    assert event.value.path().read_bytes() == b'Measurement v1'
    teacher.get_by_label('Решение по критерию').select_option('revise')
    teacher.get_by_label('Комментарий к критерию').fill('Добавьте два замера и единицы измерения.')
    teacher.get_by_role('button', name='На доработку', exact=True).click()
    teacher.get_by_role('link', name='К заданию').click()
    assert urlsplit(teacher.url).query == 'view=review'
    expect(teacher.get_by_role('link', name='На доработке: 1')).to_be_visible()

    student.goto(base + '/notifications')
    student.locator('.notifications-page').get_by_role('link', name=re.compile('К замечанию')).click()
    notice_target = urlsplit(student.url).fragment
    remark = student.locator('#' + notice_target)
    expect(remark.get_by_text('Добавьте два замера и единицы измерения.', exact=True)).to_be_visible()
    expect(remark.get_by_text('Отправка №1', exact=False)).to_be_visible()
    remark.get_by_role('link', name='К результату этапа').click()
    expect(student.get_by_role('dialog').get_by_text('Добавьте два замера и единицы измерения.', exact=False)).to_be_visible()
    note = 'Добавили три показания датчика A, единицы указаны в PDF.'
    student.get_by_label('Пояснение', exact=True).fill(note)
    student.get_by_label('Файлы для сдачи').set_input_files(
        {'name': 'too-large.pdf', 'mimeType': 'application/pdf', 'buffer': bytes(2 * 1024 * 1024 + 1)})
    student.get_by_role('button', name='Отправить учителю').click()
    expect(student.get_by_role('alert').filter(has_text='too-large.pdf')).to_be_visible()
    expect(student.get_by_label('Пояснение', exact=True)).to_have_value(note)
    files = [{'name': 'measurements.txt', 'mimeType': 'text/plain', 'buffer': b'Measurement v2'}]
    files.extend(rich_test_uploads() if real_analysis else rich_test_uploads()[:2])
    student.get_by_label('Файлы для сдачи').set_input_files(files)
    student.get_by_label('Файл для критерия').select_option(label='report.pdf')
    submit_stage(student, 'Средняя температура 20 градусов Цельсия')
    teacher.goto(assignment_url)
    if real_analysis:
        import subprocess
        teacher.get_by_role('checkbox', name='Разобрать · student-smoke').check()
        teacher.get_by_role('button', name='Разобрать выбранные сдачи').click()
        expect(teacher.get_by_text('В очереди', exact=True)).to_be_visible()
        subprocess.run([sys.executable, str(ROOT / 'class_analysis.py'), '--once'], check=True, timeout=180)
        teacher.get_by_role('link', name='Обновить статусы').click()
        expect(teacher.get_by_text('Разбор готов', exact=True)).to_be_visible()
    teacher.get_by_role('link', name='Открыть сдачу').click()
    expect(teacher.locator('[data-version="current"]').get_by_text('Средняя температура 20 градусов Цельсия')).to_be_visible()
    expect(teacher.locator('[data-version="current"]').get_by_text('Изменён', exact=False)).to_be_visible()
    expect(teacher.get_by_text('Пояснение изменено · Файл изменён')).to_be_visible()
    assert teacher.locator('.submission-attachments').get_by_role('link', name='measurements.txt').count() == 2
    if real_analysis:
        report = teacher.locator('#submission-analysis')
        expect(report.get_by_text('Критерии и цитаты')).to_be_visible()
        coverage = report.locator('details').filter(has_text='Что удалось прочитать')
        for filename in ('report.pdf', 'measurements.xlsx', 'scan.png'):
            item = coverage.get_by_role('listitem').filter(has_text=filename)
            expect(item).to_contain_text('распознан' if filename == 'scan.png' else 'прочитан')
        source = report.get_by_role('listitem').filter(has=teacher.locator('blockquote', has_text='20.1'))
        with teacher.expect_download() as event:
            source.get_by_role('link', name='report.pdf', exact=True).click()
        assert event.value.path().read_bytes() == files[1]['buffer']
        review_path = teacher.url
    teacher.get_by_label('Решение по критерию').select_option('confirmed')
    teacher.get_by_role('button', name='Принять этап').click()
    expect(teacher.get_by_text('Этап принят. Следующий этап открыт.')).to_be_visible()
    student.goto(base + project_path)
    expect(student.get_by_text('Подтверждено 1 из 1')).to_be_visible()
    student.get_by_role('link', name='Паспорт проекта').click()
    expect(student.get_by_text(criterion, exact=True)).to_be_visible()
    expect(student.locator('.passport-cover').get_by_text('Средняя температура 20 градусов Цельсия')).to_be_visible()
    assert student.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    student.get_by_role('button', name='Сохранить версию для защиты').click()
    first_passport = student.url
    expect(student.locator('.passport-cover').get_by_text('Версия 1', exact=False)).to_be_visible()
    student.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
        writeText: async text => {window.copiedPassport = text;}
    }})""")
    copy = student.get_by_role('button', name='Скопировать ссылку')
    copy.click()
    expect(student.get_by_text('Ссылка скопирована.', exact=True)).to_be_visible()
    assert student.evaluate('window.copiedPassport') == first_passport
    student.evaluate("() => Object.defineProperty(navigator, 'clipboard', {value: undefined})")
    copy.click()
    expect(student.get_by_label('Ссылка на версию')).to_have_value(first_passport)
    if real_analysis:
        teacher.goto(base + project_path + '/history')
        report = teacher.get_by_text('Мой ИИ-разбор этой сдачи')
        report.click()
        expect(teacher.get_by_text('Критерии и цитаты')).to_be_visible()
        student.goto(base + project_path + '/history')
        expect(student.get_by_text('Мой ИИ-разбор этой сдачи')).to_have_count(0)

    for stage in range(2, 6):
        student.goto(assignment_url)
        student.get_by_role('link', name='Подготовить результат').click()
        submit_stage(student, f'Готовый результат этапа {stage}', presentation=stage == 5)
        teacher.goto(assignment_url)
        teacher.get_by_role('link', name='Открыть сдачу').click()
        expect(teacher.get_by_text(f'Готовый результат этапа {stage}', exact=True)).to_be_visible()
        teacher.get_by_role('button', name='Принять этап').click()

    student.goto(base + project_path + '/passport')
    expect(student.get_by_role('link', name='Открыть презентацию')).to_have_attribute('href', 'https://example.com/slides')
    student.get_by_role('button', name='Сохранить версию для защиты').click()
    expect(student.locator('.passport-cover').get_by_text('Версия 2', exact=False)).to_be_visible()
    student.goto(first_passport)
    expect(student.get_by_role('link', name='Открыть презентацию')).to_have_count(0)

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
    assert outsider.goto(first_passport).status == 403
    for path in (urlsplit(assignment_url).path, project_path, project_path + '/history', project_path + '/assessment'):
        assert outsider.goto(base + path).status == 403, path
    if real_analysis:
        assert outsider.goto(review_path).status == 403
        print('PASS: real Ollama report, PDF original download, XLSX and OCR coverage, private report history.')
    print('PASS: criteria, revised evidence in the teacher review, mobile passport, saved versions and presentation.')
    print('PASS: class assignment, consent, five stages, return and resubmission, defense, grade and outsider isolation.')


if __name__ == '__main__':
    with classroom_server() as base, sync_playwright() as playwright:
        channel = os.environ.get('M_FLOW_BROWSER_CHANNEL')
        with playwright.chromium.launch(channel=channel) as browser:
            exercise(browser, base)
