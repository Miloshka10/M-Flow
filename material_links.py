"""Shared syntax checks for external materials; never makes network requests."""
from urllib.parse import urlsplit


def safe_web_url(value):
    if not isinstance(value, str) or not value or len(value) > 2000:
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in ('http', 'https') and parsed.hostname
                    and not parsed.username and not parsed.password
                    and (parsed.port is None or 0 < parsed.port <= 65535))
    except ValueError:
        return False
