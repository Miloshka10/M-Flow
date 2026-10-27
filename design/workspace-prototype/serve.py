"""Loopback-only static preview. Only prototype files and shared icons are served."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
PREFIX = '/design/workspace-prototype/'
FILES = {'index.html', 'model.js', 'workspace.js', 'workspace.css'}


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/static/icon-symbols.css' or path.removeprefix(PREFIX) in FILES and path.startswith(PREFIX):
            return super().do_GET()
        if path in ('/', PREFIX, PREFIX.rstrip('/')):
            self.path = PREFIX + 'index.html'
            return super().do_GET()
        self.send_error(404)

    do_HEAD = do_GET


if __name__ == '__main__':
    print('M-Flow prototype: http://localhost:5093/design/workspace-prototype/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 5093), Preview).serve_forever()
