"""Evaluate the application pipeline on the same synthetic submissions per model."""
import argparse
from dataclasses import dataclass
from http.client import HTTPConnection
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from submission_analysis import AnalysisUnavailable, analyze
from test_rich_attachment_text import scanned_work, text_pdf, workbook


@dataclass(frozen=True)
class Case:
    name: str
    goal: str
    description: str
    files: tuple
    expected: dict
    absent: tuple = ()


def cases(use_ocr):
    pdf = text_pdf('Three sensor readings: 20.1, 20.2, 20.3 Celsius.')
    sheet = workbook('<row><c r="A1" t="inlineStr"><is><t>Кабинет 12</t></is></c>'
                     '<c r="B1"><v>23.4</v></c><c r="C1"><v>24.7</v></c></row>')
    result = [
        Case('pdf', 'Проверьте наличие трёх показаний датчика.', 'Числа в отчёте.',
             ((1, 'report.pdf', pdf),), {1: ('20.1', '20.2', '20.3')}),
        Case('xlsx', 'Проверьте название кабинета и два замера.', 'Замеры в таблице.',
             ((2, 'measurements.xlsx', sheet),), {2: ('Кабинет 12', '23.4', '24.7')}),
        Case('missing', 'Найдите числовое значение погрешности прибора и единицу измерения.',
             'Замеры в отчёте. Сведения о приборе не включены.', ((1, 'report.pdf', pdf),), {},
             ('погрешност',)),
        Case('mixed', 'Найдите три показания датчика из отчёта и два замера кабинета 12 из таблицы.',
             'Отправил оба документа.', ((1, 'report.pdf', pdf), (2, 'measurements.xlsx', sheet)),
             {1: ('20.1', '20.2', '20.3'), 2: ('23.4', '24.7')}),
    ]
    if use_ocr:
        result.extend(Case(f'ocr-{kind.lower()}', 'Проверьте наличие трёх чисел на скане.',
                           'Замеры на скане.', ((3, f'scan.{kind.lower()}', scanned_work(kind)),),
                           {3: ('21.4', '22.5', '23.6')}) for kind in ('PNG', 'PDF'))
    return result


def score(case, report):
    checks = [item for item in report['checks'] if item['status'] in {'found', 'partial'}]
    matched = sum(
        any(item['attachment'] == file_id and token.casefold() in item['quote'].casefold() for item in checks)
        for file_id, tokens in case.expected.items() for token in tokens)
    false_support = sum(any(token in item['criterion'].casefold() for token in case.absent) for item in checks)
    flagged = sum(any(token in item['criterion'].casefold() for item in report['checks'] if item['status'] == 'missing')
                  for token in case.absent)
    return dict(matched=matched, false_support=false_support, missing_flagged=flagged,
                files=report['files'], checks=report['checks'])


def release_model(port, model):
    # Start each model independently even if GPU memory only holds one runner.
    connection = HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.request('POST', '/api/generate', json.dumps(dict(model=model, keep_alive=0)),
                           {'Content-Type': 'application/json'})
        with connection.getresponse() as response:
            response.read()
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+', help='Models installed in local Ollama')
    parser.add_argument('--ocr', action='store_true', help='Include PNG and scanned PDF')
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    port = int(os.environ.get('M_FLOW_OLLAMA_PORT', '11434'))
    samples = cases(args.ocr)
    failed = False
    for model in args.models:
        results = []
        for repeat in range(args.repeat):
            for case in samples:
                started = perf_counter()
                sample = dict(model=model, case=case.name, run=repeat + 1,
                              expected=sum(map(len, case.expected.values())), absent=len(case.absent))
                try:
                    report = analyze(model, dict(result=case.description, teacher_comment=''), case.files,
                                     'Проведение исследования', '', dict(title=case.name, goal=case.goal, materials=''))
                    sample.update(score(case, report))
                except AnalysisUnavailable as error:
                    failed = True
                    sample['error'] = str(error)
                sample['seconds'] = round(perf_counter() - started, 2)
                results.append(sample)
                print(json.dumps(sample), flush=True)
        print(json.dumps(dict(model=model, matched=sum(item.get('matched', 0) for item in results),
                              expected=sum(item['expected'] for item in results),
                              false_support=sum(item.get('false_support', 0) for item in results),
                              missing_flagged=sum(item.get('missing_flagged', 0) for item in results),
                              absent=sum(item['absent'] for item in results),
                              errors=sum('error' in item for item in results),
                              median_seconds=round(median(item['seconds'] for item in results), 2))), flush=True)
        try:
            release_model(port, model)
        except OSError:
            pass
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
