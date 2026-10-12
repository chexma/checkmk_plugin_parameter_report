import pytest
from cmk_addons.plugins.parameter_report.lib import profiles
from cmk_addons.plugins.parameter_report.lib.settings import SettingsError, build_settings


@pytest.fixture
def root(tmp_path):
    (tmp_path / "etc").mkdir()
    return tmp_path


def _base(root, text):
    (root / "etc/parameter_report.toml").write_text(text)


def _profile(root, name, text):
    d = root / "etc/parameter_report.d"
    d.mkdir(exist_ok=True)
    (d / f"{name}.toml").write_text(text)


@pytest.mark.parametrize("name", ["a", "kunde_a", "x-1", "a" * 40])
def test_check_name_accepts(name):
    assert profiles.check_name(name) == name


@pytest.mark.parametrize("name", ["", "A", "../x", "a/b", "a.b", "a b", "a" * 41])
def test_check_name_rejects(name):
    with pytest.raises(profiles.ProfileError, match="Profile names: a-z, 0-9, _ and -"):
        profiles.check_name(name)


def test_list_profiles(root):
    assert profiles.list_profiles(root) == []
    _profile(root, "b", "")
    _profile(root, "a", "")
    (root / "etc/parameter_report.d/old.toml.bak").write_text("")
    (root / "etc/parameter_report.d/Bad.toml").write_text("")
    assert profiles.list_profiles(root) == ["a", "b"]


def test_resolve_precedence_and_lists_replace(root):
    _base(root, 'min_split_lines = 3\nfolders = ["linux"]\ncover_company = "ACME"\n')
    _profile(root, "k", 'folders = ["db"]\ncover_customer = "K AG"\ndescription = "Kunde K"\n')
    values = profiles.resolve(root, "k")
    s = build_settings(values)
    assert s.min_split_lines == 3 and s.folders == ("db",) and s.cover_company == "ACME"
    assert s.cover_customer == "K AG" and s.profile == "k"
    assert "description" not in values
    assert build_settings(profiles.resolve(root, None)).profile == ""


def test_load_profile_description(root):
    _profile(root, "k", 'description = "Kunde K"\nmin_split_lines = 1\n')
    assert profiles.load_profile(root, "k") == ({"min_split_lines": 1}, "Kunde K")


@pytest.mark.parametrize(
    "text, message",
    [
        ('output = "/tmp/x.pdf"\n', "output is not allowed in a profile"),
        ('profile = "x"\n', "profile is not allowed in a profile"),
        ('description = "a\\nb"\n', "description"),
        (f'description = "{"x" * 201}"\n', "description"),
        ("nonsense = 1\n", "nonsense"),
        ("min_split_lines = = 1\n", "not valid TOML"),
    ],
)
def test_load_profile_errors(root, text, message):
    _profile(root, "k", text)
    with pytest.raises(SettingsError, match=message):
        profiles.load_profile(root, "k")


def test_profile_key_not_allowed_in_base(root):
    _base(root, 'profile = "x"\n')
    with pytest.raises(SettingsError, match="profile is not allowed in the configuration file"):
        profiles.base_values(root)


def test_resolve_unknown_profile_lists_available(root):
    with pytest.raises(profiles.ProfileNotFound, match="Profile x not found. Available: none"):
        profiles.resolve(root, "x")
    _profile(root, "a", "")
    with pytest.raises(profiles.ProfileNotFound, match="Available: a"):
        profiles.resolve(root, "x")


def test_manage(root):
    profiles.create(root, "a")
    assert profiles.list_profiles(root) == ["a"]
    assert profiles.load_profile(root, "a") == ({}, "")
    _profile(root, "a", "min_split_lines = 2\n")
    profiles.copy(root, "a", "b")
    assert profiles.load_profile(root, "b")[0] == {"min_split_lines": 2}
    profiles.rename(root, "b", "c")
    assert profiles.list_profiles(root) == ["a", "c"]
    profiles.delete(root, "c")
    assert profiles.list_profiles(root) == ["a"]
    assert (root / "etc/parameter_report.d/c.toml.bak").read_text() == "min_split_lines = 2\n"


def test_manage_conflicts(root):
    profiles.create(root, "a")
    profiles.create(root, "b")
    for action in (
        lambda: profiles.create(root, "a"),
        lambda: profiles.copy(root, "a", "b"),
        lambda: profiles.rename(root, "a", "b"),
    ):
        with pytest.raises(profiles.ProfileError, match="Profile .* already exists."):
            action()


def test_manage_not_found(root):
    for action in (
        lambda: profiles.copy(root, "x", "y"),
        lambda: profiles.rename(root, "x", "y"),
        lambda: profiles.delete(root, "x"),
    ):
        with pytest.raises(profiles.ProfileNotFound, match="Profile x not found."):
            action()


def test_manage_validates_names(root):
    with pytest.raises(profiles.ProfileError, match="Profile names"):
        profiles.create(root, "../evil")
    assert not (root / "etc/evil.toml").exists()


SCHED = (
    '\n[schedule]\nenabled = true\nperiod = "week"\nday = 0\ntime = "06:00"\n'
    'recipients = ["a@b.de"]\n'
)


def test_schedule_is_not_inherited_and_not_a_setting(root):
    _base(root, "min_split_lines = 3\n" + SCHED + "\n[mail]\nmax_attachment_mb = 5\n")
    _profile(root, "k", "min_split_lines = 4\n")
    assert profiles.resolve(root, "k") == {"min_split_lines": 4, "profile": "k"}
    assert profiles.load_schedule(root, None).describe() == "weekly, Mon 06:00"
    assert profiles.load_schedule(root, "k") is None
    assert profiles.load_mail(root).max_attachment_mb == 5


def test_mail_table_only_in_base(root):
    _profile(root, "k", "\n[mail]\nmax_attachment_mb = 5\n")
    with pytest.raises(SettingsError, match="mail is only allowed in the base configuration"):
        profiles.load_profile(root, "k")


def test_mail_to_not_allowed_in_base_or_profile(root):
    _base(root, 'mail_to = ["a@b.de"]\n')
    with pytest.raises(SettingsError, match="mail_to"):
        profiles.base_values(root)
    _profile(root, "k", 'mail_to = ["a@b.de"]\n')
    with pytest.raises(SettingsError, match="mail_to is not allowed in a profile"):
        profiles.load_profile(root, "k")


@pytest.mark.parametrize(
    "line",
    [
        'collect_mode = "inline"',
        "workers = 1",
        "chunk_size = 9",
        "retention_days = 30",
    ],
)
def test_run_options_only_in_the_base(root, line):
    _profile(root, "k", line + "\n")
    with pytest.raises(SettingsError, match="only allowed in the base configuration"):
        profiles.load_profile(root, "k")
    _base(root, line + "\n")
    profiles.base_values(root)  # fine in the base
