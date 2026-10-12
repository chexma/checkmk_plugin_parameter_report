"""Schedules and mail settings: the [schedule] and [mail] tables of a configuration file."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .settings import SettingsError, check_address

PERIODS = ("day", "week", "month_begin", "month_end")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_RANGE = {"day": (0, 0), "week": (0, 6), "month_begin": (1, 28), "month_end": (1, 28)}
TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
MAX_RECIPIENTS = 20
SCHEDULE_KEYS = ("enabled", "period", "day", "time", "recipients")
MAIL_KEYS = ("sender", "max_attachment_mb")


@dataclass(frozen=True)
class Schedule:
    enabled: bool
    period: str
    day: int
    time: str
    recipients: tuple[str, ...]

    def cmk_period(self) -> str | tuple[str, int]:
        return "day" if self.period == "day" else (self.period, self.day)

    def timeofday(self) -> tuple[int, int]:
        hour, minute = self.time.split(":")
        return int(hour), int(minute)

    def describe(self) -> str:
        if self.period == "day":
            return f"daily, {self.time}"
        if self.period == "week":
            return f"weekly, {WEEKDAYS[self.day]} {self.time}"
        if self.period == "month_begin":
            return f"monthly, day {self.day}, {self.time}"
        if self.day == 1:
            return f"monthly, last day, {self.time}"
        suffix = {2: "nd", 3: "rd"}.get(self.day, "th")
        return f"monthly, {self.day}{suffix} last day, {self.time}"


@dataclass(frozen=True)
class MailSettings:
    sender: str = ""
    max_attachment_mb: int = 10


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def schedule_from_toml(table: Mapping[str, object], where: str) -> Schedule:
    unknown = sorted(set(table) - set(SCHEDULE_KEYS))
    if unknown:
        raise SettingsError(f"{where}: [schedule]: unknown key(s): {', '.join(unknown)}")
    enabled = table.get("enabled", False)
    period = table.get("period", "day")
    day = table.get("day", 0)
    time = table.get("time", "")
    recipients = table.get("recipients", [])
    if not isinstance(enabled, bool):
        raise SettingsError(f"{where}: [schedule] enabled: true or false expected")
    if period not in PERIODS:
        raise SettingsError(f"{where}: [schedule] period: one of {', '.join(PERIODS)} expected")
    low, high = DAY_RANGE[period]
    if not _int(day) or not low <= day <= high:
        raise SettingsError(f"{where}: [schedule] day: {low}..{high} expected for {period}")
    if not isinstance(time, str) or not TIME.fullmatch(time):
        raise SettingsError(f"{where}: [schedule] time: HH:MM expected (00:00..23:59)")
    if not isinstance(recipients, list) or len(recipients) > MAX_RECIPIENTS:
        raise SettingsError(f"{where}: [schedule] recipients: at most {MAX_RECIPIENTS} addresses")
    for address in recipients:
        check_address(address, "recipients")
    return Schedule(enabled, period, day, time, tuple(recipients))


def schedule_to_toml(s: Schedule) -> dict[str, object]:
    return {
        "enabled": s.enabled,
        "period": s.period,
        "day": s.day,
        "time": s.time,
        "recipients": list(s.recipients),
    }


def mail_from_toml(table: Mapping[str, object]) -> MailSettings:
    unknown = sorted(set(table) - set(MAIL_KEYS))
    if unknown:
        raise SettingsError(f"[mail]: unknown key(s): {', '.join(unknown)}")
    sender = table.get("sender", "")
    size = table.get("max_attachment_mb", MailSettings.max_attachment_mb)
    if sender:
        check_address(sender, "sender")
    if not _int(size) or not 1 <= size <= 100:
        raise SettingsError("[mail] max_attachment_mb: 1..100 expected")
    return MailSettings(sender, size)


def mail_to_toml(m: MailSettings) -> dict[str, object]:
    default = MailSettings()
    return {k: getattr(m, k) for k in MAIL_KEYS if getattr(m, k) != getattr(default, k)}


def schedule_from_form(values: Mapping[str, str]) -> Schedule | None:
    """None when nothing is set (no [schedule] table is written)."""
    enabled = (values.get("enabled") or "").lower() in ("on", "1", "true", "yes")
    time = (values.get("time") or "").strip()
    raw = values.get("recipients") or ""
    recipients = [part.strip() for part in raw.split(",") if part.strip()]
    if not enabled and not time and not recipients:
        return None
    period = values.get("period") or "day"
    day_text = (values.get("day") or "").strip()
    try:
        if period == "day":
            day = 0  # a leftover value of a previous weekly/monthly setting is ignored
        else:
            day = int(day_text) if day_text else DAY_RANGE.get(period, (0, 0))[0]
    except ValueError:
        raise SettingsError(f"[schedule] day: number expected, not {day_text!r}") from None
    table = {
        "enabled": enabled,
        "period": period,
        "day": day,
        "time": time or "06:00",
        "recipients": recipients,
    }
    return schedule_from_toml(table, "Schedule")


def schedule_to_form(s: Schedule | None) -> dict[str, str]:
    if s is None:
        return {"enabled": "", "period": "day", "day": "", "time": "", "recipients": ""}
    return {
        "enabled": "on" if s.enabled else "",
        "period": s.period,
        "day": str(s.day),
        "time": s.time,
        "recipients": ", ".join(s.recipients),
    }


def mail_from_form(values: Mapping[str, str]) -> MailSettings:
    size = (values.get("max_attachment_mb") or "").strip()
    table: dict[str, object] = {}
    if (values.get("sender") or "").strip():
        table["sender"] = values["sender"].strip()
    if size:
        try:
            table["max_attachment_mb"] = int(size)
        except ValueError:
            raise SettingsError(
                f"[mail] max_attachment_mb: number expected, not {size!r}"
            ) from None
    return mail_from_toml(table)


def mail_to_form(m: MailSettings) -> dict[str, str]:
    default = MailSettings()
    return {
        "sender": m.sender,
        "max_attachment_mb": (
            "" if m.max_attachment_mb == default.max_attachment_mb else str(m.max_attachment_mb)
        ),
    }
