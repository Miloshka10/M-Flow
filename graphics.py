"""Local decorative artwork only; never represents a user's project or metrics."""


def illustration(context):
    name, classes, sizes = {
        'student': ('research-bridge', 'hero-art student-art',
                    '(max-width: 520px) 180px, (max-width: 680px) 30vw, '
                    '(max-width: 800px) 36vw, (max-width: 1080px) 20vw, 340px'),
        'auth': ('research-notebook', 'auth-art',
                 '(max-width: 520px) 260px, (max-width: 800px) 270px, 360px'),
        'teacher': ('review-study', 'hero-art teacher-art',
                    '(max-width: 800px) 1px, (max-width: 1080px) 28vw, 360px'),
    }[context]
    base = f'/static/illustrations/{name}'
    return (f'<picture class="{classes}"><source type="image/webp" '
            f'srcset="{base}-480.webp 480w, {base}-960.webp 960w" sizes="{sizes}">'
            f'<img src="{base}-480.webp" alt="" width="480" height="360" '
            'decoding="async"></picture>')
