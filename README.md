# Check Parameter Report

A Checkmk extension that documents how a site is actually monitored: one PDF
with every host and the **effective check parameters of every service**,
i.e. the thresholds and options that result from all rules, discovery and
plugin defaults together.

In Checkmk this information is only visible one service at a time (or one
host at a time with `cmk -D`). The report puts it into a single document
that can be read, searched, archived and handed over:

- **Documentation and handover:** show a customer or another team what is
  monitored and with which thresholds, with a cover page, a table of
  contents and a bookmark per host.
- **Audits and reviews:** values that a rule changed are highlighted, so
  deviations from the plugin defaults stand out; an optional mode lists only
  these changed parameters.
- **Keeping track of changes:** reports are created on a schedule and sent
  by mail, so there is a dated record of the configuration.

Besides the parameters, every host page lists addresses, tags, labels,
groups, parents or cluster nodes and data sources. Passwords are never
printed.

## Features

- Command `cmk-parameter-report` and web pages under *Setup › Maintenance ›
  Check Parameter Report* (for admins): create reports, follow the progress,
  cancel, download, delete.
- Host selection by Setup folder, host tag and host label.
- Base configuration plus profiles (e.g. one per customer) that only contain
  the differences; edited on the web page or as TOML files.
- Schedules per base/profile and mail with the PDF attached (sendmail, as for
  Checkmk notifications).
- Time-specific parameters with an appendix of the time periods used.
- PDF and mail texts in English or German, selectable date format; page
  size, columns, fonts and cover page texts are configurable.
- Fast: the Checkmk configuration is loaded only once and the pages are
  rendered in parallel. A run for 1,000 hosts takes about 30 seconds (in an
  emulated devcontainer) instead of about 40 minutes with `cmk -D` per host.

Tested with Checkmk 2.5.0p15 Ultimate. Needs only packages of the site's
Python (reportlab, pypdf).

## Installation

See [docs/installation.md](docs/installation.md).

## Usage

On the web page, or as the site user:

```bash
cmk-parameter-report                        # settings from ~/etc/parameter_report.toml
cmk-parameter-report --profile customer_a   # base + ~/etc/parameter_report.d/customer_a.toml
cmk-parameter-report --config ~/etc/parameter_report_linux.toml -v
```

All settings, the web pages, profiles, schedules and mail, status file, exit
codes, cron examples and measurements:
[docs/parameter_report.md](docs/parameter_report.md).

## Development

Tests run as the site user with the site's Python:

```bash
pytest                                   # unit tests; site tests are skipped without the test hosts
tests/fixtures/create_test_hosts.sh      # create 1,000 test hosts in the folder /spike
pytest -m site                           # integration tests against the site
black plugins tests && isort plugins tests && flake8 plugins tests
```

After a Checkmk update run the integration tests first (`pytest -m site`);
changes to the internals show up in `collector.py`.

The development environment (devcontainer with a Checkmk site) is based on
[checkmk-plugin-template](https://github.com/chexma/checkmk-plugin-template)
and is not part of this repository.

## License

GNU General Public License v3.0, see [LICENSE](LICENSE).
