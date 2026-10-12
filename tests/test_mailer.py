from datetime import datetime

import pytest
from cmk_addons.plugins.parameter_report.lib import mailer
from cmk_addons.plugins.parameter_report.lib.model import ReportSummary
from cmk_addons.plugins.parameter_report.lib.schedule_config import MailSettings
from cmk_addons.plugins.parameter_report.lib.settings import Settings

SUMMARY = ReportSummary(
    "cmk", "2.5.0p15", "ultimate", datetime(2026, 10, 10, 6, 0), 3, 40, (("db01", "KeyError: x"),)
)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "parameter_report_x.pdf"
    path.write_bytes(b"%PDF-1.4 " + b"x" * 2000)
    return path


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(mailer, "_send", lambda m, to, sender: calls.append((m, to, sender)))
    return calls


def test_compose_and_send(pdf, sent):
    settings = Settings(mail_to=("a@b.de", "c@d.de"), cover_title="Review", profile="kunde_a")
    result = mailer.send_report(pdf, SUMMARY, settings, MailSettings(sender="cmk@x.de"))
    assert result == {"to": ["a@b.de", "c@d.de"], "sent": True, "attached": True, "error": None}
    message, to, sender = sent[0]
    assert to == ["a@b.de", "c@d.de"] and sender == "cmk@x.de"
    assert message["Subject"] == "Check Parameter Report: Review (kunde_a)"
    assert message["To"] == "a@b.de, c@d.de" and message["From"] == "cmk@x.de"
    body = message.get_body(("plain",)).get_content()
    assert "Hosts: 3" in body and "Services: 40" in body and "db01" in body
    attachment = next(message.iter_attachments())
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_filename() == pdf.name
    assert attachment.get_content() == pdf.read_bytes()


def test_default_subject_and_sender(pdf, sent, monkeypatch):
    monkeypatch.setattr(mailer, "default_sender", lambda: "site@host.example")
    mailer.send_report(pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings())
    message, _to, sender = sent[0]
    assert message["Subject"] == "Check Parameter Report"  # default title: not repeated
    assert sender == "site@host.example"


def test_over_the_limit_no_attachment(pdf, sent):
    pdf.write_bytes(b"%PDF" + b"x" * (2 * 1024 * 1024))
    result = mailer.send_report(
        pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings(max_attachment_mb=1)
    )
    assert result["sent"] and not result["attached"]
    message = sent[0][0]
    assert list(message.iter_attachments()) == []
    assert "larger than the attachment limit" in message.get_body(("plain",)).get_content()


def test_send_failure(pdf, monkeypatch):
    def fail(*_args):
        raise RuntimeError("sendmail returned with exit code: 75")

    monkeypatch.setattr(mailer, "_send", fail)
    result = mailer.send_report(pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings())
    assert result["sent"] is False and result["attached"] is False
    assert result["error"].startswith("sendmail returned with exit code: 75")
    assert "without attachment" in result["error"]  # the retry failed as well


def test_encoded_size_counts_against_the_limit(pdf, sent):
    # 0.9 MB raw is 1.2 MB base64-encoded: over a 1 MB limit although raw size is below it
    pdf.write_bytes(b"%PDF" + b"x" * int(0.9 * 1024 * 1024))
    result = mailer.send_report(
        pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings(max_attachment_mb=1)
    )
    assert result["sent"] and not result["attached"]
    pdf.write_bytes(b"%PDF" + b"x" * int(0.6 * 1024 * 1024))  # 0.8 MB encoded + headroom
    result = mailer.send_report(
        pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings(max_attachment_mb=1)
    )
    assert result["attached"]


def test_retry_without_attachment(pdf, monkeypatch):
    calls = []

    def flaky(message, to, sender):
        calls.append(message)
        if len(calls) == 1:
            raise RuntimeError("message too large")

    monkeypatch.setattr(mailer, "_send", flaky)
    result = mailer.send_report(pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings())
    assert result == {"to": ["a@b.de"], "sent": True, "attached": False, "error": None}
    assert list(calls[0].iter_attachments()) and not list(calls[1].iter_attachments())
    assert "could not be sent as an attachment" in calls[1].get_body(("plain",)).get_content()


def test_send_times_out(pdf, monkeypatch):
    import time

    import cmk.utils.mail

    monkeypatch.setattr(mailer, "SEND_TIMEOUT", 1)
    monkeypatch.setattr(cmk.utils.mail, "send_mail_sendmail", lambda *a: time.sleep(10))
    result = mailer.send_report(pdf, SUMMARY, Settings(mail_to=("a@b.de",)), MailSettings())
    assert result["sent"] is False and "did not finish" in result["error"]


def test_sendmail_available(tmp_path, monkeypatch):
    monkeypatch.setattr(mailer, "SYSTEM_SENDMAIL", tmp_path / "none")
    assert not mailer.sendmail_available(tmp_path)
    (tmp_path / "local/bin").mkdir(parents=True)
    (tmp_path / "local/bin/sendmail").write_text("")
    assert mailer.sendmail_available(tmp_path)


def test_mail_in_german_with_date_format(pdf, sent):
    settings = Settings(
        mail_to=("a@b.de",), profile="kunde_a", language="de", date_format="DD.MM.YYYY"
    )
    mailer.send_report(pdf, SUMMARY, settings, MailSettings(sender="cmk@x.de", max_attachment_mb=0))
    message = sent[0][0]
    assert message["Subject"] == "Check Parameter Report (kunde_a)"
    body = message.get_body(("plain",)).get_content()
    assert body.startswith("Check Parameter Report\n")
    for expected in ["Erstellt: 10.10.2026 06:00", "Hosts mit Fehlern: 1", "Datei: ", "Anhänge"]:
        assert expected in body, expected


def test_send_test_mail(sent):
    mail = MailSettings(sender="cmk@x.de")
    mailer.send_test(["a@b.de", "c@d.de"], "en", mail, site="cmk", entry="kunde_a")
    message, to, sender = sent[0]
    assert to == ["a@b.de", "c@d.de"] and sender == "cmk@x.de"
    assert message["Subject"] == "Check Parameter Report: test mail"
    assert message["To"] == "a@b.de, c@d.de" and message["From"] == "cmk@x.de"
    body = message.get_body(("plain",)).get_content()
    assert "Site: cmk" in body and "Configuration: kunde_a" in body
    assert not list(message.iter_attachments())


def test_send_test_mail_in_german_and_failure(sent, monkeypatch):
    mailer.send_test(["a@b.de"], "de", MailSettings(sender="cmk@x.de"), site="cmk", entry="Base")
    message = sent[0][0]
    assert message["Subject"] == "Check Parameter Report: Test-Mail"
    assert "Konfiguration: Base" in message.get_body(("plain",)).get_content()

    def fail(message, to, sender):
        raise RuntimeError("sendmail: exit 75")

    monkeypatch.setattr(mailer, "_send", fail)
    with pytest.raises(RuntimeError, match="exit 75"):
        mailer.send_test(["a@b.de"], "en", MailSettings(sender="cmk@x.de"), site="cmk", entry="x")
