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
MAX_RESPONSE_BYTES = 4096


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
                                                  'Accept': 'application/json, text/plain'})
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoMailRedirect())
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                status = response.status
                code = f'EMAILJS_HTTP_{status}' if type(status) is int and 100 <= status <= 599 else 'EMAILJS_RECEIPT'
                raise MailDeliveryError(code)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('Oversized mail response')
            receipt = raw.decode('utf-8').strip()
            if receipt != 'OK' and json.loads(receipt) != 'OK':
                raise ValueError('Missing mail receipt')
    except HTTPError as exc:
        status = exc.code
        exc.close()  # Do not read, retain or display the provider's response body.
        code = f'EMAILJS_HTTP_{status}' if type(status) is int and 100 <= status <= 599 else 'EMAILJS_RECEIPT'
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
