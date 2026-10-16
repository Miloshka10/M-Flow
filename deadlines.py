"""Calendar deadlines, interpreted through the end of the day in Moscow (UTC+3)."""
from datetime import date, datetime, timedelta, timezone

MOSCOW = timezone(timedelta(hours=3), 'MSK')
TIMEZONE_LABEL = 'Москва (UTC+3)'
DEADLINE_FILTERS = {'': 'Все задачи', 'overdue': 'Просроченные', 'today': 'Сегодня',
                    'tomorrow': 'Завтра', 'week': 'В ближайшие 7 дней', 'none': 'Без срока'}
TASK_SORTS = {'priority': 'По приоритету', 'deadline': 'По ближайшему сроку'}


def format_timestamp(value):
    """Display stored SQLite UTC or offset-aware PostgreSQL time in Moscow."""
    if not isinstance(value, str) or not value:
        return 'Дата не указана'
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return f'{stamp.astimezone(MOSCOW):%d.%m.%Y %H:%M} МСК'
    except (ValueError, OverflowError):
        return 'Дата не указана'


def matches_deadline(info, mode):
    if not mode:
        return True
    if mode == 'none':
        return info['state'] == 'unset'
    if not info['active']:
        return False
    if mode == 'week':
        # Includes today through day seven; overdue work has a separate filter.
        return 0 <= info['days'] <= 7
    return info['state'] == mode


def task_sort_key(task, mode='priority'):
    if mode == 'deadline':
        due = parse_deadline(task['deadline'])
        # Missing and malformed dates come after every real date, including 9999-12-31.
        return (due is None, due or date.max, task['id'])
    return ({'urgent': 1, 'high': 2, 'normal': 3, 'low': 4}.get(task['priority'], 3), task['id'])


def business_today(now=None):
    if now is None:
        return datetime.now(MOSCOW).date()
    if now.tzinfo is None:
        raise ValueError('Для расчёта даты требуется время с часовым поясом.')
    return now.astimezone(MOSCOW).date()


def parse_deadline(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def days_word(number):
    if 11 <= number % 100 <= 14:
        return 'дней'
    return 'день' if number % 10 == 1 else 'дня' if number % 10 in (2, 3, 4) else 'дней'


def deadline_info(value, status='todo', today=None):
    due = parse_deadline(value)
    result = dict(state='unset' if not value else 'invalid', label='Без срока' if not value else 'Срок некорректен',
                  date='', display_date='', days=None, active=False, css='')
    if due is None:
        return result
    days = (due - (today or business_today())).days
    result.update(date=due.isoformat(), display_date=f'{due.day:02d}.{due.month:02d}.{due.year:04d}', days=days)
    if status == 'done':
        result.update(state='completed', label='Завершено')
    elif days < 0:
        result.update(state='overdue', label=f'Просрочено на {-days} {days_word(-days)}', active=True, css='deadline-overdue')
    else:
        state = 'today' if days == 0 else 'tomorrow' if days == 1 else 'future'
        label = 'Сегодня' if days == 0 else 'Завтра' if days == 1 else f'Осталось {days} {days_word(days)}'
        result.update(state=state, label=label, active=True, css='deadline-soon' if days <= 3 else '')
    return result


def deadline_text(info):
    return info['label'] + (' · ' + info['display_date'] if info['date'] else '')


def urgent_tasks(tasks, today=None):
    today = today or business_today()
    result = []
    for task in tasks:
        info = deadline_info(task['deadline'], task['status'], today)
        if info['active'] and info['days'] <= 3:
            result.append(dict(id=task['id'], title=task['title'], text=deadline_text(info),
                               overdue=info['state'] == 'overdue', days=info['days']))
    return sorted(result, key=lambda item: (item['days'], item['id']))
