# Changelog

## [0.1.0] - 2026-10-12

First published version.

- `cmk-parameter-report`: PDF (A4 landscape) with all hosts of a site and
  the effective parameters of all services; the Checkmk configuration is
  loaded only once and the pages are rendered in parallel. Values that a
  rule changed from the plugin default are highlighted, passwords are
  masked.
- Cover page (title, customer, company, author, classification, note),
  clickable table of contents, a bookmark per host, report-wide page
  numbers.
- Host selection by Setup folder, host tag and host label; mode *Only
  changed parameters* (`changed_only`).
- Time-specific parameters: time periods that are always active count as
  switched on, rows for other time periods show only the differences;
  appendix "Time periods" with the definitions of the time periods used.
- Configuration in `~/etc/parameter_report.toml` (base) and profiles in
  `~/etc/parameter_report.d/<name>.toml` that contain only the differences.
- Web pages under *Setup › Maintenance › Check Parameter Report* for admins
  (permission `parameter_report.use`): create reports, progress, cancel,
  download, delete; configuration page for the base and the profiles.
- Schedules per base/profile (daily, weekly, monthly) in the
  `ui-job-scheduler` and mail with the PDF attached via sendmail; *Run now*
  and *Send test mail*; `--mail` on the command line.
- Language (`en`, `de`) and date format of PDF and mail can be chosen.
- Lock (one run per site), status file, metadata per PDF, retention via
  `retention_days`.
- Packaged as an MKP (Checkmk 2.5.0 up to, but not including, 2.6.0).
