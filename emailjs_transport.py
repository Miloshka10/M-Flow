"""EmailJS HTTPS transport. Queue/rate coordination belongs to the dispatcher.

The template must escape ``{{message}}`` and bind its recipient to
``{{to_email}}``. Its sender is the connected mail service, not a body parameter.
An accepted API request is not proof of delivery to the recipient's inbox.
"""
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from account_mail import _NoMailRedirect, normalize_email, MailDeliveryError


ENDPOINT = 'https://api.emailjs.com/api/v1.0/email/send'
USER_AGENT = 'M-Flow-Mail/1.0'
MAX_RESPONSE_BYTES = 4096


def _refusal_code(status, stream):
    """Recognize fixed provider phrases; never return any response text.

    Early authorization failures need not appear in the provider's email history.
    A bounded response is used only to select a fixed local category, then dropped.
    Unrecognized text still produces only the numeric HTTP status.
    """
    fallback = f'EMAILJS_HTTP_{status}' if type(status) is int and 100 <= status <= 599 else 'EMAILJS_RECEIPT'
    if status not in (400, 401, 403):
        return fallback
    try:
        raw = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return fallback
        message = raw.decode('utf-8').strip()
        if message.startswith(('"', '{')):
            message = json.loads(message)
            if (isinstance(message, dict) and message.get('cloudflare_error') is True
                    and message.get('error_name') == 'browser_signature_banned'):
                return 'EMAILJS_CLIENT_BLOCKED'
            if not isinstance(message, str):
                return fallback
        message = message.casefold()
        if message.startswith(('<!doctype html', '<html')):
            return 'EMAILJS_HTML_REFUSAL'
        # Match short, known phrases rather than printing a provider sentence
        # that could echo credentials, a recipient, a template or a token.
        for pattern, code in (
            (r'\bapi calls are disabled for non-browser applications\b', 'EMAILJS_NON_BROWSER'),
            (r'\b(?:the )?public key is invalid\b', 'EMAILJS_PUBLIC_KEY'),
            (r'\b(?:the )?private key is invalid\b', 'EMAILJS_PRIVATE_KEY'),
            (r'\b(?:the )?access token is invalid\b', 'EMAILJS_PRIVATE_KEY'),
            (r'\b(?:the )?origin is not allowed\b', 'EMAILJS_ORIGIN'),
            (r'\brequests from this origin are (?:not allowed|blocked)\b', 'EMAILJS_ORIGIN'),
        ):
            if re.search(pattern, message):
                return code
    except (OSError, ValueError, UnicodeError, RecursionError):
        pass
    return fallback


def _action_url(origin, body):
    """Only a complete, trusted capability line can become the template button.

    Account names can contain URLs. They must never supply the action URL, nor
    may an unrelated URL or a partial/ambiguous match become a clickable button.
    """
    pattern = re.compile(re.escape(origin) + r'/(?:reset-password|email/confirm)#token=[A-Za-z0-9_-]{43}\Z')
    matches = [line.removesuffix('\r') for line in body.split('\n')
               if pattern.fullmatch(line.removesuffix('\r'))]
    return matches[0] if len(matches) == 1 else ''


def send_emailjs(settings, recipient, subject, body):
    """Send one template request with private authorization and no retries.

    Never forward credentials/capabilities via redirects or expose the provider
    response in diagnostics. All calls must already hold the shared rate gate.
    """
    try:
        recipient = normalize_email(recipient)
    except ValueError:
        raise MailDeliveryError('EMAILJS_RECIPIENT') from None
    try:
        payload = dict(service_id=settings.service_id, template_id=settings.template_id,
                       user_id=settings.public_key, accessToken=settings.api_key,
                       template_params=dict(to_email=recipient, subject=subject,
                                            message=body, action_url=_action_url(settings.origin, body)))
        request = Request(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          method='POST', headers={'Content-Type': 'application/json',
                                                  'Accept': 'application/json, text/plain',
                                                  'User-Agent': USER_AGENT})
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoMailRedirect())
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise MailDeliveryError(_refusal_code(response.status, response))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('Oversized mail response')
            receipt = raw.decode('utf-8').strip()
            if receipt != 'OK' and json.loads(receipt) != 'OK':
                raise ValueError('Missing mail receipt')
    except HTTPError as exc:
        try:
            code = _refusal_code(exc.code, exc)
        finally:
            exc.close()
        raise MailDeliveryError(code) from None
    except URLError as exc:
        reason = exc.reason
        code = ('EMAILJS_TLS' if isinstance(reason, ssl.SSLError) else
                'EMAILJS_TIMEOUT' if isinstance(reason, TimeoutError) else 'EMAILJS_NETWORK')
        raise MailDeliveryError(code) from None
    except ssl.SSLError:
        raise MailDeliveryError('EMAILJS_TLS') from None
    except TimeoutError:
        raise MailDeliveryError('EMAILJS_TIMEOUT') from None
    except OSError:
        raise MailDeliveryError('EMAILJS_NETWORK') from None
    except (ValueError, UnicodeError, RecursionError):
        raise MailDeliveryError('EMAILJS_RECEIPT') from None
