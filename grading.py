"""Критерии из ведомости оценивания проектов 10–11 классов."""

import json

RUBRIC_VERSION = "school-project-45-v1"
CRITERIA = (
    ("1", "Актуальность темы", 3),
    ("2", "Логичность и полнота материалов", 3),
    ("3", "Практическая реализуемость", 6),
    ("4", "Внедрение в практику", 5),
    ("5", "Обоснование методов, оборудование", 3),
    ("6", "Практические навыки", 5),
    ("7", "Самостоятельность", 4),
    ("8", "Аргументация выводов", 5),
    ("9", "Ответы на вопросы", 5),
    ("10", "Культура выступления", 3),
    ("11", "Качество презентации", 2),
    ("12", "Отзыв вуза / предприятия", 1),
)
MAX_SCORE = sum(maximum for _, _, maximum in CRITERIA)


def parse_scores(form, publish=False):
    scores = {}
    for key, label, maximum in CRITERIA:
        raw = form.get(f"criterion_{key}", "").strip()
        if not raw:
            if publish:
                raise ValueError(f"Заполните критерий «{label}» перед публикацией.")
            scores[key] = None
        elif raw not in {str(value) for value in range(maximum + 1)}:
            raise ValueError(f"Критерий «{label}»: допустимы целые баллы от 0 до {maximum}.")
        else:
            scores[key] = int(raw)
    return scores


def summarize(scores):
    complete = all(scores.get(key) is not None for key, _, _ in CRITERIA)
    total = sum(scores.get(key) or 0 for key, _, _ in CRITERIA)
    grade = None
    if complete:
        grade = 5 if total >= 39 else 4 if total >= 30 else 3 if total >= 21 else 2
    return total, grade


def load_scores(row):
    return json.loads(row["scores_json"]) if row else {}
