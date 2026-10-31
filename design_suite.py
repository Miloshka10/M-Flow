"""Presentation helpers. No database access or workflow decisions."""
from html import escape


def initials(name):
    return ''.join(word[0] for word in str(name).split()[:2]).upper() or '—'


def project_cover(name, project_id):
    tone = ('sage', 'blue', 'gold')[int(project_id) % 3]
    word = str(name).split()[0] if str(name).split() else 'Проект'
    return f'<div class="ds-cover ds-{tone}" aria-hidden="true"><span>{escape(word[:24].upper())}</span></div>'


def stage_track(accepted):
    return (f'<div class="ds-stage-track" role="img" aria-label="Принято {accepted} из 5 этапов">'
            + ''.join('<span class="accepted"></span>' if n < accepted else '<span></span>' for n in range(5)) + '</div>')
