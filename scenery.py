"""Passive page artwork. No user data, workflow state or database access."""


CONTEXTS = frozenset(('workshop', 'together', 'review'))
LAYOUTS = frozenset(('workspace', 'auth', 'access'))


def scenery(context, layout='workspace'):
    """Keep decorative markup separate from forms and the accessible document."""
    if context not in CONTEXTS or layout not in LAYOUTS:
        raise ValueError('Unknown scenery context or layout')
    return '<div class="mf-scenery" aria-hidden="true"><div class="mf-scenery-art"></div></div>'
