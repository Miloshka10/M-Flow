"""Configuration-only preflight: no app import, database, network or email.

Only variable NAMES and boolean checks are printed. Successful configuration
is not evidence of delivery, legal readiness, browser QA or a live release.
"""
import json
import os

from account_mail import MailSettings, normalize_email, public_origin


def configuration_report():
    provider = os.environ.get('M_FLOW_MAIL_PROVIDER', 'smtp').strip().lower()
    mail = MailSettings.from_environment()
    required = ['M_FLOW_PUBLIC_URL', 'M_FLOW_MAIL_FROM']
    if provider in ('brevo', 'emailjs'):
        required += ['M_FLOW_MAIL_API_KEY']
        if provider == 'emailjs':
            required += ['M_FLOW_MAIL_PUBLIC_KEY', 'M_FLOW_MAIL_SERVICE_ID', 'M_FLOW_MAIL_TEMPLATE_ID']
    elif provider == 'smtp':
        required += ['M_FLOW_MAIL_HOST', 'M_FLOW_MAIL_USER', 'M_FLOW_MAIL_PASSWORD']
    code = os.environ.get('M_FLOW_TEACHER_CODE', '')
    try:
        support = bool(normalize_email(os.environ.get('M_FLOW_SUPPORT_EMAIL', '')))
    except ValueError:
        support = False
    checks = dict(mail_configured=mail is not None,
                  public_https_origin_configured=bool(public_origin(os.environ.get('M_FLOW_PUBLIC_URL', ''))),
                  teacher_registration_code_configured=24 <= len(code) <= 256 and code == code.strip(),
                  operator_name_present=bool(os.environ.get('M_FLOW_OPERATOR_NAME', '').strip()),
                  support_address_valid=support)
    return dict(configuration_only=True, configuration_complete=all(checks.values()),
                mail_provider=provider if provider in ('smtp', 'brevo', 'emailjs') else 'unsupported',
                checks=checks, missing_mail_variables=[name for name in required if not os.environ.get(name)],
                external_checks_required=['sender_verification', 'real_test_delivery', 'browser_qa',
                                          'data_processing_policy', 'live_release_verification'])


def main():
    report = configuration_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['configuration_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
