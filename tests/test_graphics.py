"""Artwork contract: local, decorative, responsive and bounded without Pillow at runtime."""
from html.parser import HTMLParser
from pathlib import Path
import unittest
from xml.etree import ElementTree

from graphics import illustration

ROOT = Path(__file__).resolve().parents[1]


class ArtTags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class GraphicsTests(unittest.TestCase):
    def test_contexts_use_distinct_local_responsive_images_with_reserved_space(self):
        names = set()
        for context in ('student', 'auth', 'teacher'):
            tags = ArtTags(illustration(context)).tags
            self.assertEqual([tag for tag, _ in tags], ['picture', 'source', 'img'])
            source, image = tags[1][1], tags[2][1]
            self.assertEqual(source['type'], 'image/webp')
            self.assertIn('480w, ', source['srcset'])
            self.assertIn('960w', source['srcset'])
            self.assertIn('max-width:', source['sizes'])
            self.assertTrue(image['src'].startswith('/static/illustrations/'))
            self.assertTrue(image['src'].endswith('-480.webp'))
            self.assertEqual((image['width'], image['height']), ('480', '360'))
            self.assertEqual(image['alt'], '')
            self.assertEqual(image['decoding'], 'async')
            names.add(image['src'])
        self.assertEqual(len(names), 3)
        with self.assertRaises(KeyError):
            illustration('../private')

    def test_webp_files_preserve_alpha_dimensions_and_per_image_budget(self):
        total = 0
        for name in ('research-bridge', 'research-notebook', 'review-study'):
            for width in (480, 960):
                data = (ROOT / 'static' / 'illustrations' / f'{name}-{width}.webp').read_bytes()
                self.assertEqual(data[:4], b'RIFF')
                self.assertEqual(data[8:16], b'WEBPVP8X')
                self.assertEqual(int.from_bytes(data[4:8], 'little') + 8, len(data))
                self.assertTrue(data[20] & 0x10)  # VP8X alpha flag.
                self.assertEqual(int.from_bytes(data[24:27], 'little') + 1, width)
                self.assertEqual(int.from_bytes(data[27:30], 'little') + 1, width * 3 // 4)
                self.assertGreater(len(data), 10000)
                self.assertLess(len(data), 150000 if width == 960 else 50000)
                total += len(data)
        self.assertLess(total, 600000)

    def test_small_vectors_are_original_passive_and_do_not_embed_external_content(self):
        for name, viewbox in (('blank-study', '0 0 480 360'), ('team-study', '0 0 48 48')):
            data = (ROOT / 'static' / 'illustrations' / f'{name}.svg').read_bytes()
            self.assertLess(len(data), 2000)
            root = ElementTree.fromstring(data)
            self.assertEqual(root.attrib['viewBox'], viewbox)
            for element in root.iter():
                self.assertIn(element.tag.split('}')[-1], ('svg', 'path', 'circle'))
                self.assertFalse(any(key.lower().startswith('on') or key.endswith('href') for key in element.attrib))

    def test_graphics_css_is_scoped_and_does_not_add_animation_or_remote_resources(self):
        css = (ROOT / 'static' / 'graphics.css').read_text(encoding='utf-8')
        for prohibited in ('animation:', '@import', 'url(', 'background:', 'font-size:', 'grid-template', 'filter:'):
            self.assertNotIn(prohibited, css)
        self.assertIn('aspect-ratio: 4 / 3', css)
        self.assertIn('.student-workspace .hero-art', css)
        self.assertIn('.teacher-workspace .hero-art', css)
        self.assertIn('@media (max-width: 520px)', css)


if __name__ == '__main__':
    unittest.main()
