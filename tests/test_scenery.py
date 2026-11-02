"""Decorative page scenery: local assets, theme selection and secured forms."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

from scenery import scenery

ROOT = Path(__file__).resolve().parents[1]


class SceneryTags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def webp_size_and_alpha(data):
    """Read standard WebP headers without a runtime imaging dependency."""
    if data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        raise ValueError('Not WebP')
    offset = 12
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], 'little')
        chunk = data[offset + 8:offset + 8 + length]
        if kind == b'VP8X' and len(chunk) >= 10:
            return (int.from_bytes(chunk[4:7], 'little') + 1,
                    int.from_bytes(chunk[7:10], 'little') + 1,
                    bool(chunk[0] & 0x10))
        if kind == b'VP8L' and len(chunk) >= 5 and chunk[0] == 0x2f:
            bits = int.from_bytes(chunk[1:5], 'little')
            return ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1,
                    bool(bits & (1 << 28)))
        offset += 8 + length + (length % 2)
    raise ValueError('Missing WebP dimensions')


class SceneryAssetTests(unittest.TestCase):
    def test_layer_is_passive_and_rejects_untrusted_names(self):
        for context in ('workshop', 'together', 'review'):
            for layout in ('workspace', 'auth', 'access'):
                with self.subTest(context=context, layout=layout):
                    tags = SceneryTags(scenery(context, layout=layout)).tags
                    self.assertTrue(tags)
                    self.assertEqual(tags[0][1].get('aria-hidden'), 'true')
                    for tag, attrs in tags:
                        self.assertEqual(tag, 'div')
                        self.assertFalse(set(attrs) & {'tabindex', 'contenteditable', 'onclick'})
                        self.assertFalse(any(key.startswith('on') for key in attrs))
        for bad in ('../private', '" onmouseover="alert(1)', '', None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    scenery(bad)
                with self.assertRaises(ValueError):
                    scenery('workshop', layout=bad)

    def test_theme_assets_are_transparent_local_and_bounded(self):
        total = 0
        files = []
        for context in ('workshop', 'together', 'review'):
            for tone in ('day', 'night'):
                for width in (960, 1600):
                    files.append((f'{context}-{tone}-{width}.webp', width))
        files.extend((f'mobile-{tone}-720.webp', 720) for tone in ('day', 'night'))
        for name, width in files:
            with self.subTest(name=name):
                data = (ROOT / 'static' / 'scenery' / name).read_bytes()
                self.assertEqual(int.from_bytes(data[4:8], 'little') + 8, len(data))
                actual_width, height, alpha = webp_size_and_alpha(data)
                self.assertEqual(actual_width, width)
                self.assertGreater(height, 0)
                self.assertLessEqual(height, width * 2)
                self.assertTrue(alpha, 'Opaque artwork would add a rectangle in the dark theme')
                self.assertLess(len(data), 300_000)
                total += len(data)
        self.assertLess(total, 2_000_000)

    def test_scenery_styles_do_not_fetch_remote_content_or_animate(self):
        css = (ROOT / 'static' / 'scenery.css').read_text(encoding='utf-8')
        self.assertNotIn('@import', css)
        self.assertNotRegex(css, r'(?i)animation\s*:')
        urls = re.findall(r'url\(\s*[\'"]?([^\)\'"\s]+)', css)
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue(url.startswith('/static/scenery/'), url)
            self.assertNotIn('..', url)
            self.assertTrue((ROOT / url.lstrip('/')).is_file(), url)


class SceneryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Composition avoids rerunning every inherited application test here.
        import test_teacher_cabinet as fixtures
        cls.fixture_type = fixtures.TeacherCabinetTests
        cls.fixture_type.setUpClass()
        cls.module = cls.fixture_type.module

    @classmethod
    def tearDownClass(cls):
        cls.fixture_type.tearDownClass()

    def setUp(self):
        self.fixture = self.fixture_type()
        self.fixture.setUp()
        self.client = self.fixture.client
        from account_mail import MailSettings
        from account_recovery_cases import Mailbox
        self.module.app.config.update(
            MAIL_SETTINGS=MailSettings('smtp.invalid', 587, 'fake', 'fake',
                                       'sender@example.org', 'http://localhost', 'starttls'),
            MAIL_DISPATCHER=Mailbox())

    def assert_scene(self, response, context, layout):
        self.assertEqual(response.status_code, 200)
        tags = SceneryTags(response.text).tags
        body = next(attrs for tag, attrs in tags if tag == 'body')
        self.assertEqual(body.get('data-scenery'), context)
        self.assertEqual(body.get('data-scenery-layout'), layout)
        layers = [attrs for tag, attrs in tags if 'mf-scenery' in attrs.get('class', '').split()]
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].get('aria-hidden'), 'true')
        self.assertIn('href="/static/scenery.css', response.text)
        # Decorating the document must not displace secured POST form tokens.
        from test_teacher_cabinet import FormTokens
        forms = FormTokens(response.text)
        self.assertTrue(forms.meta)
        for form in forms.forms:
            if form['method'] == 'post':
                self.assertEqual(form['tokens'], [forms.meta])

    def test_public_role_and_recovery_pages_choose_their_scene(self):
        for path, context, layout in (
            ('/login', 'workshop', 'auth'),
            ('/register', 'workshop', 'auth'),
            ('/teacher/login', 'review', 'auth'),
            ('/teacher/register', 'review', 'auth'),
            ('/forgot-password', 'workshop', 'access'),
            ('/reset-password', 'workshop', 'access'),
            ('/email/confirm', 'workshop', 'access'),
        ):
            with self.subTest(path=path):
                self.assert_scene(self.client.get(path), context, layout)

    def test_workspace_contexts_follow_roles_and_collaboration_pages(self):
        for user, home in ((3, '/'), (1, '/teacher')):
            self.fixture.sign_in(user)
            default = 'review' if user == 1 else 'workshop'
            for path, context in ((home, default), ('/profile', default),
                                  ('/project/2', default), ('/groups', 'together'),
                                  ('/teams', 'together')):
                with self.subTest(user=user, path=path):
                    self.assert_scene(self.client.get(path), context, 'workspace')
        self.assertEqual(self.client.get('/project/3').status_code, 403)

    def test_user_words_cannot_select_the_account_recovery_layout(self):
        conn = self.module.get_db()
        try:
            conn.execute("UPDATE projects SET name='auth-recovery' WHERE id=2")
            conn.commit()
        finally:
            conn.close()
        self.fixture.sign_in(3)
        before = self.fixture.database_snapshot()
        for path in ('/', '/project/2'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn('auth-recovery', response.text)
                self.assert_scene(response, 'workshop', 'workspace')
        self.assertEqual(self.fixture.database_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
