"""Configuration profiles: ~/etc/parameter_report.d/<name>.toml on top of the base file.

A profile holds only what differs from the base; resolve() merges both, the
profile winning (lists replace). Run keys never come from a profile.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config_file
from .schedule_config import MailSettings, Schedule, mail_from_toml, schedule_from_toml
from .settings import (
    DEFAULT_CONFIG,
    GLOBAL_KEYS,
    NAME_RULE,
    PROFILE_NAME,
    RUN_KEYS,
    SettingsError,
)

PROFILE_DIR = Path("etc/parameter_report.d")  # relative to OMD_ROOT
MAX_DESCRIPTION = 200


class ProfileError(SettingsError):
    """Invalid profile name or profile operation; the message is shown as is."""


class ProfileNotFound(ProfileError):
    pass


def check_name(name: str) -> str:
    if not PROFILE_NAME.fullmatch(name or ""):
        raise ProfileError(NAME_RULE)
    return name


def profile_dir(root: Path) -> Path:
    return root / PROFILE_DIR


def profile_path(root: Path, name: str) -> Path:
    return profile_dir(root) / f"{check_name(name)}.toml"


def list_profiles(root: Path) -> list[str]:
    directory = profile_dir(root)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.toml") if PROFILE_NAME.fullmatch(p.stem))


def _not_found(root: Path, name: str) -> ProfileNotFound:
    available = ", ".join(list_profiles(root)) or "none"
    return ProfileNotFound(f"Profile {name} not found. Available: {available}")


def base_values(root: Path) -> dict[str, object]:
    path = root / DEFAULT_CONFIG
    raw = config_file.load(path) if path.exists() else {}
    for key in ("profile", "mail_to"):
        if key in raw:
            raise SettingsError(f"{key} is not allowed in the configuration file")
    _tables(raw, "Base configuration", base=True)
    return config_file.settings_part(raw)


def _tables(raw: dict[str, object], where: str, base: bool) -> None:
    """Validate the [schedule] and [mail] tables (they are not settings)."""
    if "schedule" in raw:
        schedule_from_toml(raw["schedule"], where)
    if base and "mail" in raw:
        mail_from_toml(raw["mail"])


def _raw_base(root: Path) -> dict[str, object]:
    path = root / DEFAULT_CONFIG
    return config_file.load(path) if path.exists() else {}


def load_schedule(root: Path, name: str | None) -> Schedule | None:
    """The [schedule] of the base configuration or of a profile (never inherited)."""
    if name:
        path = profile_path(root, name)
        if not path.is_file():
            raise _not_found(root, name)
        raw = config_file.load(path, extra=("description",))
        where = f"Profile {name}"
    else:
        raw = _raw_base(root)
        where = "Base configuration"
    return schedule_from_toml(raw["schedule"], where) if "schedule" in raw else None


def load_mail(root: Path) -> MailSettings:
    return mail_from_toml(_raw_base(root).get("mail", {}))


def load_profile(root: Path, name: str) -> tuple[dict[str, object], str]:
    """Values set by the profile (without description) and its description."""
    path = profile_path(root, name)
    if not path.is_file():
        raise _not_found(root, name)
    raw = config_file.load(path, extra=("description",))
    if "mail" in raw:
        raise ProfileError("mail is only allowed in the base configuration")
    _tables(raw, f"Profile {name}", base=False)
    description = raw.get("description", "")
    values = config_file.settings_part(raw)
    if (
        not isinstance(description, str)
        or len(description) > MAX_DESCRIPTION
        or "\n" in description
        or "\r" in description
    ):
        raise ProfileError(
            f"Profile {name}: description: one line of at most {MAX_DESCRIPTION} characters "
            "expected"
        )
    for key in GLOBAL_KEYS:
        if key in values:
            raise ProfileError(f"Profile {name}: {key} is only allowed in the base configuration")
    for key in RUN_KEYS:
        if key in values:
            raise ProfileError(f"Profile {name}: {key} is not allowed in a profile")
    return values, description


def resolve(root: Path, name: str | None) -> dict[str, object]:
    """Base values, overlaid by the profile's values; marks the run's profile."""
    values = dict(base_values(root))
    if name:
        own, _description = load_profile(root, name)
        values.update(own)
        values["profile"] = name
    return values


def _existing(root: Path, name: str) -> Path:
    path = profile_path(root, name)
    if not path.is_file():
        raise _not_found(root, name)
    return path


def _free(root: Path, name: str) -> Path:
    path = profile_path(root, name)
    if path.exists():
        raise ProfileError(f"Profile {name} already exists.")
    return path


def create(root: Path, name: str) -> None:
    path = _free(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_atomic(path, config_file.dump({}, f"Profile {name}"), backup=False)


def copy(root: Path, source: str, target: str) -> None:
    src = _existing(root, source)
    shutil.copy2(src, _free(root, target))


def rename(root: Path, old: str, new: str) -> None:
    src = _existing(root, old)
    os.rename(src, _free(root, new))


def delete(root: Path, name: str) -> None:
    path = _existing(root, name)
    os.replace(path, path.with_name(path.name + ".bak"))
