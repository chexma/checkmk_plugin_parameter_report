import io
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
import requests
from pypdf import PdfReader

pytestmark = pytest.mark.site

SITE = os.environ.get("OMD_SITE", "cmk")
BASE = f"http://localhost:5000/{SITE}/check_mk/"
API = BASE + "api/1.0"
ADMIN = ("cmkadmin", "cmkadmin")
GUEST = ("prguest", "Pr-guest-Secret-2026!")
PAGE = BASE + "parameter_report.py"
CONFIG_PAGE = BASE + "parameter_report_config.py"
CREATE_PAGE = BASE + "parameter_report_create.py"
STATUS = BASE + "ajax_parameter_report_status.py"
DOWNLOAD = BASE + "parameter_report_download.py"
CONFIG = Path(os.environ.get("OMD_ROOT", "/omd/sites/cmk")) / "etc/parameter_report.toml"


def _login(user, password):
    session = requests.Session()
    data = {"_username": user, "_password": password, "_login": "1", "_origtarget": "index.py"}
    session.post(BASE + "login.py", data=data, timeout=60).raise_for_status()
    assert any(c.name.startswith("auth_") for c in session.cookies), f"login of {user} failed"
    return session


@pytest.fixture(scope="module")
def admin(site_with_test_hosts):
    subprocess.run(["omd", "reload", "apache"], check=True, capture_output=True)
    time.sleep(2)
    session = _login(*ADMIN)
    assert "Current run" in session.get(PAGE, timeout=60).text, "page not loaded"
    return session


@pytest.fixture(scope="module")
def guest(admin):
    body = {
        "username": GUEST[0],
        "fullname": "Parameter report test guest",
        "roles": ["user"],
        "auth_option": {"auth_type": "password", "password": GUEST[1]},
    }
    headers = {"Authorization": f"Bearer {ADMIN[0]} {ADMIN[1]}", "Accept": "application/json"}
    r = requests.post(
        API + "/domain-types/user_config/collections/all", json=body, headers=headers, timeout=60
    )
    assert r.status_code in (200, 400, 409), r.text  # 400/409: exists from an earlier run
    return _login(*GUEST)


def _tokens(page_html, form_name="parameter_report_start"):
    """Transaction id and CSRF token of the named form (None: the first form of the page)."""
    pattern = rf'<form[^>]*name="{form_name}".*?</form>' if form_name else r"<form.*?</form>"
    form = re.search(pattern, page_html, re.S)
    assert form, f"form {form_name} missing"

    def value(name):
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', form.group(0)) or re.search(
            rf'value="([^"]*)"[^>]*name="{name}"', form.group(0)
        )
        return match.group(1) if match else None

    return {"_transid": value("_transid"), "_csrf_token": value("_csrf_token")}


def _post(session, data):
    """Start on the create page; cancel and delete on the report page."""
    if data.get("_action") == "start":
        url, tokens = CREATE_PAGE, _tokens(session.get(CREATE_PAGE, timeout=60).text)
    else:
        url, tokens = PAGE, _tokens(session.get(PAGE, timeout=60).text, None)
    assert tokens["_transid"], "no transaction id in the form"
    return session.post(url, data={**tokens, **data}, timeout=60)


def _post_config(session, data, params=None):
    page = session.get(CONFIG_PAGE, params=params or {}, timeout=60).text
    tokens = _tokens(page, "parameter_report_config")
    return session.post(CONFIG_PAGE, data={**tokens, **data}, timeout=60)


def _status(session):
    return session.get(STATUS, timeout=60).json()["result"]


def _wait(session, condition, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        status = _status(session)
        if condition(status):
            return status
        time.sleep(1)
    raise AssertionError(f"timeout, last status {status}")


def _report_names(session):
    return set(re.findall(r"parameter_report_download\.py\?name=([^\"&]+)", session.get(PAGE).text))


def test_page_and_menu_for_admin(admin):
    page = admin.get(PAGE, timeout=60).text
    for text in ["Check Parameter Report", "Current run", "Reports", "Create report"]:
        assert text in page, text
    assert "parameter_report_config.py" in page  # button Configuration
    assert "parameter_report_create.py" in page  # button Create report
    # starting a report lives on its own page
    assert "New report" not in page and 'name="_start"' not in page
    create = admin.get(CREATE_PAGE, timeout=60).text
    for text in ["Create report", "Profile", "Hosts", "Back to reports"]:
        assert text in create, text
    assert 'name="_start"' in create and 'name="profile"' in create
    # no setting fields: they live in the base and the profiles
    for field in ("hosts", "folders", "workers", "page_size", "cover_title", "cover_note"):
        assert f'name="{field}"' not in create, field
    menu = admin.get(BASE + "side.py", timeout=60).text  # main menu incl. Setup entries
    assert "analyze_config" in menu and "parameter_report.py" in menu


def test_non_admin_is_rejected(guest):
    page = guest.get(PAGE, timeout=60).text
    assert "Current run" not in page and "permi" in page.lower()  # "not permitted"
    create = guest.get(CREATE_PAGE, timeout=60).text
    assert 'name="_start"' not in create and "permi" in create.lower()
    assert "parameter_report.py" not in guest.get(BASE + "side.py", timeout=60).text
    config = guest.get(CONFIG_PAGE, timeout=60).text
    assert "Base (all reports)" not in config and "permi" in config.lower()
    assert guest.get(STATUS, timeout=60).json()["result_code"] != 0
    download = guest.get(DOWNLOAD, params={"name": "parameter_report_x.pdf"}, timeout=60)
    assert not download.content.startswith(b"%PDF")


def test_status_json(admin):
    status = _status(admin)
    assert {"state", "title", "active", "crashed", "percent", "report"} <= set(status)


def _test_profile(name, text):
    PROFILES.mkdir(exist_ok=True)
    (PROFILES / f"{name}.toml").write_text(text)


def test_start_download_delete(admin, clean_profiles):
    before = _report_names(admin)
    _test_profile(
        "t",
        'hosts = ["spike-0001", "spike-0002"]\ncover_title = "Site test <review>"\n'
        'cover_note = "-first line\\nsecond line"\nclassification = "Internal"\n',
    )
    page = admin.get(CREATE_PAGE, params={"profile": "t"}, timeout=60).text
    assert "hosts: spike-0001, spike-0002" in page and "Site test &lt;review&gt;" in page
    data = {"_action": "start", "_start": "1", "profile": "t"}
    assert "Report started." in _post(admin, data).text
    status = _wait(admin, lambda s: s["state"] in ("done", "failed") and not s["active"])
    assert status["state"] == "done", status
    name = status["report"]
    run_file = Path(os.environ["OMD_ROOT"]) / "var/parameter_report" / name.replace(".pdf", ".toml")
    assert 'cover_title = "Site test <review>"' in run_file.read_text()
    assert name not in before and name in _report_names(admin)
    pdf = admin.get(DOWNLOAD, params={"name": name}, timeout=60)
    assert pdf.content.startswith(b"%PDF")
    assert "attachment" in pdf.headers["Content-Disposition"]
    front = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "Site test <review>" in front and "-first line" in front and "second line" in front
    assert "Site test &lt;review&gt;" in admin.get(PAGE, timeout=60).text  # Title column
    _post(admin, {"_action": "delete", "_delete": "1", "name": name})
    assert name not in _report_names(admin)
    assert not run_file.exists()


def test_cancel_running_report(admin, clean_config):
    CONFIG.write_text("workers = 1\n")
    data = {"_action": "start", "_start": "1"}
    assert "Report started." in _post(admin, data).text
    _wait(admin, lambda s: s["active"] and s["state"] in ("collecting", "rendering"))
    assert "Cancel requested." in _post(admin, {"_action": "cancel", "_cancel": "1"}).text
    status = _wait(admin, lambda s: not s["active"])
    assert status["state"] == "cancelled", status


def test_action_without_transid_is_ignored(admin):
    before = _status(admin)
    response = admin.post(CREATE_PAGE, data={"_action": "start", "_start": "1"}, timeout=60)
    assert "Create report" in response.text and "Report started." not in response.text
    time.sleep(3)
    assert _status(admin)["started_at"] == before["started_at"]


def test_download_rejects_bad_names(admin):
    for name in ["../../etc/passwd", "parameter_report_x.pdf", "status.json"]:
        response = admin.get(DOWNLOAD, params={"name": name}, timeout=60)
        assert response.status_code == 404 and not response.content.startswith(b"%PDF"), name


def test_no_link_injection(admin):
    evil = '<a href="https://evil.example/x">Session expired, log in</a>'
    page = admin.get(PAGE, params={"_message": evil}, timeout=60).text
    assert "https://evil.example" not in page or '<a href="https://evil.example' not in page
    response = _post(admin, {"_action": "start", "_start": "1", "profile": evil})
    assert "Profile names" in response.text and "Report started." not in response.text
    assert '<a href="https://evil.example' not in response.text


@pytest.fixture
def clean_config():
    saved = CONFIG.read_bytes() if CONFIG.exists() else None
    CONFIG.unlink(missing_ok=True)
    yield
    if saved is None:
        CONFIG.unlink(missing_ok=True)
    else:
        CONFIG.write_bytes(saved)
    Path(str(CONFIG) + ".bak").unlink(missing_ok=True)


def test_save_configuration(admin, clean_config):
    page = admin.get(CONFIG_PAGE, timeout=60).text
    assert "Base (all reports)" in page and "Back to reports" in page
    data = {
        "_action": "save",
        "_save": "1",
        "mtime": "0",
        "cover_company": "ACME GmbH",
        "retention_days": "7",
    }
    assert "Configuration saved." in _post_config(admin, data).text
    assert 'cover_company = "ACME GmbH"' in CONFIG.read_text()
    page = admin.get(CREATE_PAGE, timeout=60).text
    assert "ACME GmbH" in page and 'name="cover_company"' not in page  # summary of the base
    page = admin.get(CONFIG_PAGE, timeout=60).text
    assert re.search(
        r'name="retention_days"[^>]*value="7"|value="7"[^>]*name="retention_days"', page
    )


def test_save_configuration_invalid(admin, clean_config):
    data = {"_action": "save", "_save": "1", "mtime": "0", "chunk_size": "0", "retention_days": "4"}
    response = _post_config(admin, data)
    assert "chunk_size: 0 is out of range" in response.text and not CONFIG.exists()
    assert re.search(
        r'name="retention_days"[^>]*value="4"|value="4"[^>]*name="retention_days"', response.text
    )


def test_save_configuration_conflict(admin, clean_config):
    CONFIG.write_text("retention_days = 1\n")
    response = _post_config(
        admin, {"_action": "save", "_save": "1", "mtime": "0", "retention_days": "2"}
    )
    assert "changed meanwhile" in response.text and CONFIG.read_text() == "retention_days = 1\n"


PROFILES = CONFIG.parent / "parameter_report.d"


@pytest.fixture
def clean_profiles(clean_config):
    import shutil

    backup = PROFILES.with_name("parameter_report.d.testbackup")
    saved = PROFILES.exists()
    if saved:
        shutil.move(PROFILES, backup)
    yield
    shutil.rmtree(PROFILES, ignore_errors=True)
    if saved:
        shutil.move(backup, PROFILES)


def _profile_action(admin, button, name="", source=""):
    data = {"_action": "profile", button: "1", "name": name, "profile": source}
    return _post_config(admin, data)


def test_profile_lifecycle(admin, clean_profiles):
    assert "Profile created." in _profile_action(admin, "_new", "kunde_a").text
    assert (PROFILES / "kunde_a.toml").exists()
    page = admin.get(CONFIG_PAGE, params={"profile": "kunde_a"}, timeout=60).text
    assert "Profile kunde_a" in page and 'name="description"' in page
    data = {
        "_action": "save",
        "_save": "1",
        "profile": "kunde_a",
        "mtime": str((PROFILES / "kunde_a.toml").stat().st_mtime_ns),
        "description": "Kunde A",
        "cover_customer": "A AG",
    }
    assert "Configuration saved." in _post_config(admin, data, {"profile": "kunde_a"}).text
    assert 'cover_customer = "A AG"' in (PROFILES / "kunde_a.toml").read_text()
    assert "Kunde A" in admin.get(CONFIG_PAGE, timeout=60).text  # description in the table
    page = admin.get(CREATE_PAGE, params={"profile": "kunde_a"}, timeout=60).text
    assert "A AG" in page and "Kunde A" in page  # summary of the profile
    assert "Profile copied." in _profile_action(admin, "_copy", "kunde_b", "kunde_a").text
    assert "Profile renamed." in _profile_action(admin, "_rename", "kunde_c", "kunde_b").text
    assert sorted(p.name for p in PROFILES.glob("*.toml")) == ["kunde_a.toml", "kunde_c.toml"]
    assert "Profile deleted." in _profile_action(admin, "_delete", "", "kunde_c").text
    assert (PROFILES / "kunde_c.toml.bak").exists()


def test_profile_actions_validate_names(admin, clean_profiles):
    response = _profile_action(admin, "_new", "../evil")
    assert "Profile names: a-z, 0-9, _ and -" in response.text
    assert not (CONFIG.parent / "evil.toml").exists()
    _profile_action(admin, "_new", "a")
    assert "Profile a already exists." in _profile_action(admin, "_new", "a").text
    assert "Profile x not found." in _profile_action(admin, "_delete", "", "x").text
    page = admin.get(CONFIG_PAGE, params={"profile": "../x"}, timeout=60).text
    assert "Profile names" in page and "Base (all reports)" in page


def test_start_with_profile(admin, clean_profiles):
    _test_profile("kunde_a", 'hosts = ["spike-0001"]\n')
    data = {
        "_action": "start",
        "_start": "1",
        "profile": "kunde_a",
        "hosts": "spike-0002",  # not a field any more: ignored
    }
    assert "Report started." in _post(admin, data).text
    status = _wait(admin, lambda s: s["state"] in ("done", "failed") and not s["active"])
    assert status["state"] == "done", status
    run_file = Path(os.environ["OMD_ROOT"]) / "var/parameter_report" / status["report"]
    assert 'hosts = ["spike-0001"]' in run_file.with_suffix(".toml").read_text()
    page = admin.get(PAGE, timeout=60).text
    assert re.search(r'<span[^>]*title="Profile"[^>]*>kunde_a</span>', page)  # tag in the list
    _post(admin, {"_action": "delete", "_delete": "1", "name": status["report"]})


def _checkbox(page_html, name):
    tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', page_html)
    assert tag, f"checkbox {name} missing"
    return " checked" in tag.group(0).lower()


def test_profile_form_shows_inherited_checkbox(admin, clean_profiles):
    CONFIG.write_text("portrait = true\n")
    _profile_action(admin, "_new", "x")
    page = admin.get(CONFIG_PAGE, params={"profile": "x"}, timeout=60).text
    assert _checkbox(page, "portrait")
    assert not _checkbox(page, "changed_only")
    # Saving the form as rendered (portrait ticked, changed_only not submitted)
    data = {
        "_action": "save",
        "_save": "1",
        "profile": "x",
        "mtime": str((PROFILES / "x.toml").stat().st_mtime_ns),
        "portrait": "on",
    }
    assert "Configuration saved." in _post_config(admin, data, {"profile": "x"}).text
    assert "portrait" not in (PROFILES / "x.toml").read_text()
    # The base form: built-in false, file true
    assert _checkbox(admin.get(CONFIG_PAGE, timeout=60).text, "portrait")
    CONFIG.write_text("retention_days = 1\n")
    assert not _checkbox(admin.get(CONFIG_PAGE, timeout=60).text, "portrait")


def test_profile_with_own_false_checkbox(admin, clean_profiles):
    CONFIG.write_text("portrait = true\n")
    _profile_action(admin, "_new", "x")
    (PROFILES / "x.toml").write_text("portrait = false\n")
    page = admin.get(CONFIG_PAGE, params={"profile": "x"}, timeout=60).text
    assert not _checkbox(page, "portrait")


def test_start_with_forged_profile(admin, clean_profiles):
    before = _report_names(admin)
    response = _post(
        admin,
        {"_action": "start", "_start": "1", "profile": "../evil", "hosts": "spike-0001"},
    )
    assert "Profile names" in response.text and "Report started." not in response.text
    assert _report_names(admin) == before
    response = _post(
        admin, {"_action": "start", "_start": "1", "profile": "nope", "hosts": "spike-0001"}
    )
    assert "Report started." not in response.text and _report_names(admin) == before


def test_broken_profile_and_base_messages(admin, clean_profiles):
    page = admin.get(CONFIG_PAGE, timeout=60).text
    assert "Cron jobs using the old name will fail." not in page  # no profile yet
    _profile_action(admin, "_new", "bad")
    (PROFILES / "bad.toml").write_text("no_such_key = 1\n")
    page = admin.get(CONFIG_PAGE, timeout=60).text
    assert "invalid" in page and "Cron jobs using the old name will fail." in page
    CONFIG.write_text("chunk_size = 0\n")
    (PROFILES / "bad.toml").write_text("")
    page = admin.get(CONFIG_PAGE, params={"profile": "bad"}, timeout=60).text
    assert "Base configuration:" in page and "Fix the base first." in page
    assert "Saving replaces the file" not in page


SENDMAIL = Path(os.environ.get("OMD_ROOT", "/omd/sites/cmk")) / "local/bin/sendmail"
MAILBOX = Path("/tmp/parameter_report_test_mail")


@pytest.fixture
def fake_sendmail():
    """~/local/bin is the repo's bin/: create the fake and always remove it."""
    SENDMAIL.write_text(f'#!/bin/sh\necho "ARGS $*" >> {MAILBOX}\ncat >> {MAILBOX}\n')
    SENDMAIL.chmod(0o755)
    MAILBOX.unlink(missing_ok=True)
    yield MAILBOX
    SENDMAIL.unlink(missing_ok=True)
    MAILBOX.unlink(missing_ok=True)


# Disabled: a live schedule must not start reports on the dev site (only the scheduler test)
SCHED_FORM = {
    "sched_present": "1",
    "sched_enabled": "",
    "sched_period": "week",
    "sched_day": "0",
    "sched_time": "06:00",
    "sched_recipients": "a@kunde.de",
}


def test_schedule_on_config_page(admin, clean_profiles):
    _profile_action(admin, "_new", "kunde_a")
    mtime = str((PROFILES / "kunde_a.toml").stat().st_mtime_ns)
    # Enabled, but for tomorrow's weekday: the last appointment is six days ago, never due
    weekday = (datetime.now().weekday() + 1) % 7
    form = {**SCHED_FORM, "sched_enabled": "on", "sched_day": str(weekday)}
    data = {"_action": "save", "_save": "1", "profile": "kunde_a", "mtime": mtime, **form}
    assert "Configuration saved." in _post_config(admin, data, {"profile": "kunde_a"}).text
    assert "[schedule]" in (PROFILES / "kunde_a.toml").read_text()
    page = admin.get(CONFIG_PAGE, timeout=60).text
    assert f"weekly, {('Mon Tue Wed Thu Fri Sat Sun').split()[weekday]} 06:00" in page
    assert re.search(r"\d{4}-\d{2}-\d{2} 06:00", page)


def test_send_test_mail_button(admin, clean_profiles, fake_sendmail):
    _profile_action(admin, "_new", "kunde_a")
    page = admin.get(CONFIG_PAGE, params={"profile": "kunde_a"}, timeout=60).text
    assert 'name="_test_mail"' not in page and "Save recipients first" in page
    mtime = str((PROFILES / "kunde_a.toml").stat().st_mtime_ns)
    data = {"_action": "save", "_save": "1", "profile": "kunde_a", "mtime": mtime, **SCHED_FORM}
    assert "Configuration saved." in _post_config(admin, data).text
    page = admin.get(CONFIG_PAGE, params={"profile": "kunde_a"}, timeout=60).text
    assert 'name="_test_mail"' in page
    saved = (PROFILES / "kunde_a.toml").read_text()
    data = {"_action": "save", "_test_mail": "1", "profile": "kunde_a", "mtime": "0"}
    assert "Test mail sent to the saved recipients." in _post_config(admin, data).text
    assert (PROFILES / "kunde_a.toml").read_text() == saved  # the button does not save
    mail = fake_sendmail.read_text()
    assert "a@kunde.de" in mail and "Subject: Check Parameter Report: test mail" in mail
    assert "Configuration: kunde_a" in mail


def test_run_now_mails_the_report(admin, clean_profiles, fake_sendmail):
    _profile_action(admin, "_new", "kunde_a")
    mtime = str((PROFILES / "kunde_a.toml").stat().st_mtime_ns)
    data = {
        "_action": "save",
        "_save": "1",
        "profile": "kunde_a",
        "mtime": mtime,
        "hosts": "spike-0001",
        **SCHED_FORM,
    }
    _post_config(admin, data, {"profile": "kunde_a"})
    response = _post_config(admin, {"_action": "profile", "_run_now": "1", "profile": "kunde_a"})
    assert "Report started." in response.text
    status = _wait(admin, lambda s: s["state"] in ("done", "failed") and not s["active"])
    assert status["state"] == "done", status
    mail = fake_sendmail.read_text()
    assert "a@kunde.de" in mail and "Subject: Check Parameter Report" in mail
    assert "application/pdf" in mail
    _post(admin, {"_action": "delete", "_delete": "1", "name": status["report"]})


def test_scheduler_runs_due_schedule(admin, clean_profiles, fake_sendmail):
    """The cron job in ui-job-scheduler starts a schedule that is due now."""
    subprocess.run(["omd", "restart", "ui-job-scheduler"], check=True, capture_output=True)
    now = datetime.now()
    PROFILES.mkdir(exist_ok=True)
    (PROFILES / "due.toml").write_text(
        'hosts = ["spike-0001"]\n\n[schedule]\nenabled = true\nperiod = "day"\n'
        f'day = 0\ntime = "{now:%H:%M}"\nrecipients = ["due@kunde.de"]\n'
    )
    end = time.time() + 180
    while time.time() < end and not (
        fake_sendmail.exists() and "due@kunde.de" in fake_sendmail.read_text()
    ):
        time.sleep(5)
    assert "due@kunde.de" in fake_sendmail.read_text()
    (PROFILES / "due.toml").unlink()  # no second run
    status = _wait(admin, lambda s: s["state"] in ("done", "failed") and not s["active"])
    assert status["state"] == "done", status
    _post(admin, {"_action": "delete", "_delete": "1", "name": status["report"]})


def test_new_report_mail_checkbox(admin, clean_profiles):
    _profile_action(admin, "_new", "kunde_a")
    mtime = str((PROFILES / "kunde_a.toml").stat().st_mtime_ns)
    _post_config(
        admin,
        {"_action": "save", "_save": "1", "profile": "kunde_a", "mtime": mtime, **SCHED_FORM},
        {"profile": "kunde_a"},
    )
    page = admin.get(CREATE_PAGE, params={"profile": "kunde_a"}, timeout=60).text
    assert 'name="send_mail"' in page
    # the base has no recipients
    assert 'name="send_mail"' not in admin.get(CREATE_PAGE, timeout=60).text


def test_error_state_save_keeps_schedule_and_mail(admin, clean_config):
    CONFIG.write_text(
        'chunk_size = 0\n\n[schedule]\nenabled = false\nperiod = "day"\nday = 0\n'
        'time = "05:00"\nrecipients = ["keep@kunde.de"]\n\n[mail]\nmax_attachment_mb = 7\n'
    )
    page = admin.get(CONFIG_PAGE, timeout=60).text
    assert 'name="sched_present"' not in page and 'name="mail_present"' not in page
    mtime = str(CONFIG.stat().st_mtime_ns)
    data = {"_action": "save", "_save": "1", "profile": "", "mtime": mtime, "chunk_size": "50"}
    assert "Configuration saved." in _post_config(admin, data).text
    saved = CONFIG.read_text()
    assert "[schedule]" in saved and "keep@kunde.de" in saved
    assert "[mail]" in saved and "max_attachment_mb = 7" in saved


def test_run_options_only_on_the_base(admin, clean_profiles):
    _profile_action(admin, "_new", "k")
    profile_page = admin.get(CONFIG_PAGE, params={"profile": "k"}, timeout=60).text
    assert "Advanced settings" not in profile_page and 'name="workers"' not in profile_page
    base_page = admin.get(CONFIG_PAGE, timeout=60).text
    assert "Advanced settings" in base_page and 'name="workers"' in base_page
    # site-wide settings come last, after schedule and mail
    assert base_page.index("Schedule and mail") < base_page.index("Advanced settings")
    assert "Only changed parameters" in base_page


def test_summary_is_escaped(admin, clean_profiles):
    evil = '<a href="https://evil.example/x">click</a>'
    _test_profile("e", f'description = "{evil.replace(chr(34), chr(92) + chr(34))}"\n')
    page = admin.get(CREATE_PAGE, params={"profile": "e"}, timeout=60).text
    assert '<a href="https://evil.example' not in page and "&lt;a href=" in page
