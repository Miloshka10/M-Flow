"""Web encoding only: preserve the generated composition and transparent alpha."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / 'static' / 'illustrations'
OUTPUT.mkdir(parents=True, exist_ok=True)
for name in ('research-bridge', 'research-notebook', 'review-study'):
    with Image.open(ROOT / f'{name}-source.png') as original:
        source = original.convert('RGBA')
    if source.width * 3 != source.height * 4:
        raise ValueError(f'{name}: expected a 4:3 original, no cropping allowed')
    for width in (480, 960):
        height = width * 3 // 4
        image = source.resize((width, height), Image.Resampling.LANCZOS)
        path = OUTPUT / f'{name}-{width}.webp'
        image.save(path, 'WEBP', quality=88, method=6, exact=True)
        print(f'{path.name}: {width}x{height}, {path.stat().st_size} bytes; alpha preserved')
