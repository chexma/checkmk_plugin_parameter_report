# Installation

## Requirements

- Checkmk 2.5.x (tested with 2.5.0p15 Ultimate). The extension uses Checkmk
  internals; from 2.6 on Checkmk disables the package until it has been
  tested there and `version.usable_until` in the manifest has been raised.
- No additional Python packages: reportlab and pypdf are part of Checkmk.
- For mail: a working `sendmail` on the Checkmk server, as for Checkmk
  notifications (e.g. Postfix).

## Install the MKP

Download `parameter_report-<version>.mkp` from the
[releases](https://github.com/chexma/checkmk_plugin_parameter_report/releases).

On the command line, as the site user:

```bash
mkp add parameter_report-<version>.mkp       # e.g. parameter_report-0.1.0.mkp
mkp enable parameter_report <version>
omd reload apache                # loads the web pages
omd restart ui-job-scheduler     # schedules: the scheduler loads plugins only at start
```

Or in the GUI under *Setup › Maintenance › Extension packages*; Checkmk then
reloads the GUI itself. For schedules `omd restart ui-job-scheduler` is
still needed.

## What the package contains

| Repository | Site |
|---|---|
| `plugins/parameter_report/lib/` | `~/local/lib/python3/cmk_addons/plugins/parameter_report/lib/` |
| `plugins/parameter_report/gui/` | `~/local/lib/python3/cmk_addons/plugins/parameter_report/gui/` |
| `lib/gui/plugins/wato/parameter_report.py` | `~/local/lib/python3/cmk/gui/plugins/wato/parameter_report.py` (loader of the web pages) |
| `bin/cmk-parameter-report` | `~/local/bin/cmk-parameter-report` |

## After the installation

- The pages are under *Setup › Maintenance › Check Parameter Report*. They
  need the permission `parameter_report.use`, which by default only the role
  "admin" has (*Setup › Roles & permissions*).
- On the command line: `cmk-parameter-report` as the site user.
- *Analyze configuration* shows a "deprecated" warning for the loader file:
  Checkmk 2.5 has no stable interface for custom pages. If the pages are
  missing after a Checkmk update, the error is in `~/var/log/web.log`.

Files the extension writes at runtime:

| Path | Content |
|---|---|
| `~/etc/parameter_report.toml`, `~/etc/parameter_report.d/` | base configuration and profiles |
| `~/var/parameter_report/` | reports, metadata, status and schedule state |
| `~/var/log/parameter_report.log` | output of runs started by the web page or the scheduler |

These files are not removed when the package is disabled or removed.

Usage and all settings: [parameter_report.md](parameter_report.md).
