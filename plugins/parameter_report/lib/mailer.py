"""Mail a finished report like Checkmk's report scheduler: sendmail, PDF attached."""

from __future__ import annotations

import signal
from collections.abc import Sequence
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from .i18n import text, timestamp
from .model import ReportSummary
from .render import title as report_title
from .schedule_config import MailSettings
from .settings import Settings

SYSTEM_SENDMAIL = Path("/usr/sbin/sendmail")
MAX_LISTED_ERRORS = 20
SEND_TIMEOUT = 300  # seconds; sendmail runs while the report run lock is held
HEADROOM = 64 * 1024  # headers and the text part, on top of the encoded attachment


def default_sender() -> str:
    from cmk.utils.mail import default_from_address  # Checkmk's default, as for notifications

    return default_from_address()


def sendmail_available(root: Path) -> bool:
    return (root / "local/bin/sendmail").exists() or SYSTEM_SENDMAIL.exists()


def _send(message: EmailMessage, recipients: Sequence[str], sender: str) -> None:
    from cmk.utils.mail import MailString, send_mail_sendmail

    def on_alarm(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"sendmail did not finish within {SEND_TIMEOUT} s")

    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(SEND_TIMEOUT)
    try:  # subprocess.run kills the child when the exception passes through it
        send_mail_sendmail(message, MailString(",".join(recipients)), MailString(sender))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _body(
    pdf: Path,
    summary: ReportSummary,
    settings: Settings,
    attached: bool,
    size_mb: float,
    send_failed: bool = False,
) -> str:
    lang = settings.language
    lines = [
        text(lang, "title"),
        "",
        f"{text(lang, 'site')}: {summary.site}",
        f"{text(lang, 'created')}: {timestamp(summary.generated_at, settings.date_format)}",
        f"{text(lang, 'hosts')}: {summary.host_count}",
        f"{text(lang, 'services')}: {summary.service_count}",
        f"{text(lang, 'hosts_with_errors')}: {len(summary.failed_hosts)}",
    ]
    for host, _error in summary.failed_hosts[:MAX_LISTED_ERRORS]:
        lines.append(f"  - {host}")
    if len(summary.failed_hosts) > MAX_LISTED_ERRORS:
        lines.append("  - …")
    lines += ["", f"{text(lang, 'mail_file')}: {pdf.name}"]
    if not attached:
        key = "mail_send_failed" if send_failed else "mail_too_large"
        lines += ["", text(lang, key, size=f"{size_mb:.1f}")]
    return "\n".join(lines) + "\n"


def compose(
    pdf: Path,
    summary: ReportSummary,
    settings: Settings,
    mail: MailSettings,
    send_failed: bool = False,
) -> tuple[EmailMessage, bool]:
    """The message and whether the PDF is attached (send_failed: retry without it)."""
    name = text(settings.language, "title")
    subject = report_title(settings)  # the default title is the product name: not repeated
    if subject != name:
        subject = text(settings.language, "mail_subject", title=subject)
    if settings.profile:
        subject += f" ({settings.profile})"
    size = pdf.stat().st_size
    # The limit applies to the mail as sent: base64 makes the PDF a third larger
    attached = not send_failed and size * 4 / 3 + HEADROOM <= mail.max_attachment_mb * 1024 * 1024
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail.sender or default_sender()
    message["To"] = ", ".join(settings.mail_to)
    message["Date"] = formatdate(localtime=True)
    message.set_content(_body(pdf, summary, settings, attached, size / 1024 / 1024, send_failed))
    if attached:
        message.add_attachment(
            pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name
        )
    return message, attached


def send_test(
    recipients: Sequence[str], language: str, mail: MailSettings, *, site: str, entry: str
) -> None:
    """Short mail without a report, to check the way to the recipients; raises on failure."""
    message = EmailMessage()
    message["Subject"] = text(language, "mail_test_subject")
    message["From"] = mail.sender or default_sender()
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(text(language, "mail_test_body", site=site, entry=entry) + "\n")
    _send(message, list(recipients), str(message["From"]))


def send_report(pdf: Path, summary: ReportSummary, settings: Settings, mail: MailSettings) -> dict:
    result: dict = {
        "to": list(settings.mail_to),
        "sent": False,
        "attached": False,
        "error": None,
    }
    recipients = list(settings.mail_to)
    try:
        message, attached = compose(pdf, summary, settings, mail)
        try:
            _send(message, recipients, str(message["From"]))
        except TimeoutError:
            raise  # a hanging sendmail would hang again
        except Exception as first:
            if not attached:
                raise
            # often the mail is too large for the MTA: retry once without the attachment
            message, _ = compose(pdf, summary, settings, mail, send_failed=True)
            try:
                _send(message, recipients, str(message["From"]))
            except Exception as second:
                raise RuntimeError(
                    f"{str(first) or type(first).__name__} "
                    f"(retry without attachment failed too: {str(second) or type(second).__name__})"
                ) from None
            attached = False
        result["attached"] = attached
    except Exception as exc:  # sendmail missing, exit code, unreadable PDF: the report stays
        result["error"] = str(exc) or type(exc).__name__
    else:
        result["sent"] = True
    return result
