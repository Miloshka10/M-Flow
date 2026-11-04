"""Small, escaped presentation primitives; no workflow or access decisions."""
from html import escape


_STATE_TONES = {
    'work': 'neutral', 'draft': 'neutral', 'review': 'info',
    'revision': 'warning', 'accepted': 'success', 'grade': 'success',
}


def state_badge(label, state, *, result=False):
    """Keep existing selector hooks and the caller's actual state label."""
    tone = _STATE_TONES[state]
    css = 'result-state' if result else f'ui-state {state}'
    return f'<span class="{css}" data-ui-tone="{tone}">{escape(str(label))}</span>'


def notice(message, *, error=False):
    """Text-only notification; never treat a user message as HTML."""
    css, role, tone = ('error', 'alert', 'danger') if error else ('success', 'status', 'success')
    return f'<div class="{css}" role="{role}" data-ui-tone="{tone}">{escape(str(message))}</div>'
