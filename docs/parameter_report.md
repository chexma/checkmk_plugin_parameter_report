# cmk-parameter-report

PDF with all hosts of the site and the effective parameters of all services.
Loads the Checkmk configuration once; tested with Checkmk 2.5.0p15 Ultimate.
The command line is the only place that creates reports; the web page starts
it, reads the status file and offers the PDFs. All settings live in a base
configuration (`~/etc/parameter_report.toml`) and optionally in profiles
(`~/etc/parameter_report.d/<name>.toml`); both can be edited on the web page.

Installation: see [installation.md](installation.md).

## Web page

Three pages under *Setup › Maintenance*: the report page *Check Parameter
Report* (`parameter_report.py`), the page *Create report*
(`parameter_report_create.py`) and the configuration page
(`parameter_report_config.py`, button *Configuration*).

### Report page

- **Permission:** `parameter_report.use`, by default only the role "admin"
  (and roles derived from it); can be changed under *Setup › Roles &
  permissions*. Other users see neither the menu entry nor the pages.
- **Create report:** button at the top left, opens the page *Create report*.
  While a report is running, a note is shown instead.
- **Configuration:** button, opens the configuration page.
- **Current run:** phase, progress bar (updated every 3 s), start time and
  user, button *Cancel* (sends SIGTERM, clean abort), after the end the button
  *Download PDF*. The coloured edge shows the state (yellow: running or
  cancelled, green: done, red: failed). If a run was killed, a warning
  appears; a new start is possible.
- **Reports:** all reports of this site from `~/var/parameter_report/`
  (including those created by cron), full width, newest first:
  - *Report*: title of the cover page, profile as a badge, below it the time
    and who started it ("scheduler" for scheduled runs). A click on the title
    opens the details (settings, time per phase, memory, mail result, file
    name). The report of the last run is marked *Latest*.
  - *Scope*: hosts and services; "only changed parameters" if the report
    contains only values set by rules.
  - *Result*: state in Checkmk colours. For OK nothing else is shown;
    otherwise the reason ("3 host(s) with errors", "exit code 1", "mail
    failed"). Below it the runtime (start to end), the size and the mail
    state.
  - Buttons *Download* and *Delete report* (deletes PDF, metadata and run
    file).

  Opening the page deletes reports older than `retention_days` of the base
  (see *Retention*), so old reports disappear even when no new run takes
  place.

### Page Create report

- Selection *Profile* (Base = only the base) with a summary of the selected
  entry (description, host selection, title, customer, company, schedule,
  recipients) and the link *Edit* to the configuration page. Changing the
  selection reloads the page.
- No settings are entered here: the run uses the saved values of the base or
  the profile. For a different run (e.g. a single host) create a profile.
  Target directory and status file are fixed (default paths).
- Checkbox *Send by mail to the profile's recipients* (only visible if the
  base or the selected profile has recipients): the run mails the report.
- *Create report* starts the run and leads back to the report page, where the
  progress is shown. While a report is running, a note replaces the button.
  *Back to reports* leads back without starting.

### Configuration page

Page `parameter_report_config.py`; the link *Back to reports* leads back.

- Table: the row "Base (all reports)" and one row per profile with *Edit*,
  *Copy*, *Rename* and *Delete*; *New profile* creates a profile. A broken
  profile is shown as invalid in the table.
- Below it the form of the selected entry (*Hosts to include*, *Report
  content*, *Layout*, *Cover page*, *Schedule and mail*, for the base also
  *Mail settings* and at the very bottom *Advanced settings*). *Advanced
  settings* exist only for the base: they apply to the whole site. Profiles
  additionally have *Description* (one line, at most 200 characters). In a
  profile the placeholders show the inherited value from the base or the
  built-in default.
- *Save* rewrites only this file: comments are not kept, the previous version
  is stored next to it as `<file>.bak`. Empty fields are not saved;
  checkboxes and selection fields only if they differ from the inherited
  value; filled text and number fields are saved. The keys `output`,
  `status_file` and `started_by` set by hand in the base are kept when saving.
- If the file was changed in the meantime (checked per file), the message
  "… changed meanwhile" appears and nothing is written. If the file is
  invalid, a warning appears and the form is empty.
- Saving the base is refused if it would make a profile invalid.
- The table has the columns *Schedule* (e.g. "weekly, Mon 06:00" or "–"),
  *Next run* (only for an active schedule) and *Last run* (time and result:
  OK, hosts with errors, failed, mail failed; for invalid settings, a failed
  start or a skipped date the error). The result comes from the newest
  scheduler report of the entry, but only if that report started at most one
  minute before the last run or later; otherwise it says "running" (a report
  is running right now) or "no report (see ~/var/log/parameter_report.log)",
  never the result of an older run. If the file of a profile or its
  `[schedule]` cannot be read, its row shows "invalid – edit to repair" as
  the description and "–" in the schedule columns. *Run now* starts a run
  immediately, like the scheduler, with mail; the next scheduled date stays
  unchanged. The button exists for entries with recipients or an active
  schedule.
- Section *Schedule and mail* (collapsed): *Enabled*, *Period*, *Day*,
  *Time*, *Recipients* (comma separated) and *Test mail*: the button *Send
  test mail* sends a short mail without a report to the saved recipients of
  this entry (sender from *Mail settings*, language of the entry); unsaved
  changes in the form do not count, and nothing is saved. Afterwards the top
  of the page shows "Test mail sent to the saved recipients." or the error of
  sendmail. Without saved recipients or without sendmail, a note replaces the
  button. Only in the base there is also *Mail settings* with *Sender
  address* and *Maximum attachment size (MB)*. Warnings: "sendmail is
  missing; …" if no sendmail is found, and "Scheduler not available: …" if
  the cron job is not registered. If the file is broken, the schedule fields
  are missing, so that saving does not delete the schedule.
- *Delete* removes the profile, `<name>.toml.bak` stays. *Rename* breaks cron
  calls that use the old name.

### General

- Runs are started in the background with `setsid`, independent of the web
  server; the output goes to `~/var/log/parameter_report.log`.
- Checkmk 2.5 has no stable interface for custom pages. The page is loaded
  via `~/local/lib/python3/cmk/gui/plugins/wato/parameter_report.py`;
  *Analyze configuration* shows a "deprecated" warning for it. If loading
  fails after an update, the error is in `~/var/log/web.log` and the rest of
  the GUI keeps working.
- The report contains only hosts of the site it runs on (with distributed
  monitoring, not the hosts of the remote sites).

All program texts (pages, messages, help) are English; the fixed texts of
the PDF and the mail can be switched to German (see *Language and date
format*).

## Command line

As the site user:

```
cmk-parameter-report [--profile NAME | --config FILE] [--mail] [-v]
```

Without options `~/etc/parameter_report.toml` is read, with `--config`
exactly `FILE`, with `--profile` the base plus the profile (see *Profiles*).
`--profile` and `--config` together are an error; an unknown profile ends
with "Profile NAME not found. Available: …" (exit code 1). An invalid name,
including `--profile ""`, ends with the naming rule "Profile names: a-z, 0-9,
_ and -, at most 40 characters.". If the base is missing, the built-in
defaults apply (all hosts, output to `~/var/parameter_report/`). With
`--config` the file must exist and be valid. `-v` prints the time per phase
and the peak memory per process. `--mail` is described under *Schedules and
mail*. There are no further options; all settings are listed in the tables
below (column *Setting*) and are keys of the configuration file. Variants
(e.g. for cron) are profiles.

### Selection

| Setting | Default | Meaning |
|---|---|---|
| `hosts` | all | only these hosts |
| `folders` | – | Setup folders including subfolders, e.g. `linux/web`; several: OR |
| `host_tags` | – | several: all must match |
| `host_labels` | – | several: all must match |
| `changed_only` | off | only values set by rules; services without such values are left out |

All criteria apply together. Hosts named in `hosts` that are outside the
selection are reported as "unknown host".

### Language and date format

On the web page in the section *Report content*; can be set in the base and
per profile, independently of each other.

| Setting | Default | Meaning |
|---|---|---|
| `language` | `en` | language of the fixed texts in PDF and mail: `en`, `de` |
| `date_format` | `YYYY-MM-DD` | times in PDF and mail: `YYYY-MM-DD` (2026-10-10 15:14), `DD.MM.YYYY` (10.10.2026 15:14), `DD/MM/YYYY` (10/10/2026 15:14), `MM/DD/YYYY` (10/10/2026 03:14 PM) |

All fixed texts are translated: labels of the cover page (e.g.
*Classification* → *Klassifizierung*), column headers, host fields, table of
contents, footers, notes for empty hosts, the default title, and subject and
text of the mail. Not translated are data from Checkmk (service names,
parameter names and values, tags, labels), texts you enter yourself (title,
company, classification value …) and the web pages.

The texts are in `lib/locales/<language>.toml` (in the plugin directory).
To add a language: copy `en.toml`, e.g. as `fr.toml`, translate the values
(keys and `{placeholders}` stay), add the file to the manifest `package`; it
then appears in the selection automatically. A test checks that every
language has exactly the same keys and placeholders.

### Time-specific parameters

Rules with a time-specific specification (default value plus values per time
period) are evaluated as Checkmk does: of the entries that are active at the
moment, the first one applies, key by key.

- Time periods that are always active count as permanently switched on: the
  built-in `24X7` and custom time periods that cover 00:00–24:00 on all
  seven days, exclude nothing and have no shorter exceptions. Their values
  are in the main row, with the prefix "[Always active: Always (24X7)]"
  (German "Immer aktiv").
- For every other time period one row "» Time period Alias (ID): …" with only
  the values that differ from the main row; without a difference the row is
  left out. With `changed_only` all these differences stay.
- Appendix "Time periods" (German "Zeiträume") after the last host: the time
  periods used and those they exclude, with weekdays, times, exceptions
  (`YYYY-MM-DD`) and excluded time periods; with a bookmark and as the last
  entry of the table of contents. A time period that a rule names but that
  does not exist (any more) is listed as "not defined, never active".
- Not evaluated: whether two custom time periods overlap in time.

### Run options

`collect_mode`, `workers`, `chunk_size` and `retention_days` apply to the
whole site and belong only in the base; in a profile they are an error
("… is only allowed in the base configuration").

| Setting | Default | Meaning |
|---|---|---|
| `output` | `~/var/parameter_report/parameter_report_<date>_<time>_<id>.pdf` | target PDF |
| `collect_mode` | `subprocess` | load the configuration in a separate process (`inline`: in the main process, faster, more memory) |
| `workers` | 2 (1 with fewer than 4 CPUs) | render processes; more = faster, but more RAM |
| `chunk_size` | 50 | hosts per render chunk |
| `retention_days` | 0 | delete reports older than N days (0 = never, at most 3650) |
| `status_file` | `~/var/parameter_report/status.json` | status of the run |
| `started_by` | site user | for status and metadata |

### Layout

| Setting | Default |
|---|---|
| `page_size` | `A4` |
| `portrait` | off (landscape) |
| `margin_mm` | 12 |
| `font_size` | 6.5 |
| `line_height` | 8.0 (between 1× and 3× `font_size`) |
| `column_widths` | `34,22,14,16` (characters for service, plugin, item, service groups; parameters get the rest, at least 20) |
| `min_split_lines` | 2 |
| `max_param_chars` | 20000 |
| `max_cover_error_chars` | 500 (at most 5000) |
| `highlight_color` | `#fde68a` |
| `toc` | on (table of contents, see *Content*) |

### Cover page

The report starts with a cover page: the title, below it the customer, then
a table with company, author, classification, creation time, site, Checkmk
version, number of hosts/services and mode, below it the note, the legend
and the hosts with errors. Values that are not set are left out; without
them only the overview remains. Many hosts with errors continue the cover
page on further pages. The title is also the PDF title (metadata).

| Setting | Meaning |
|---|---|
| `cover_title` | title; empty = "Check Parameter Report" |
| `cover_company` | company that creates the report |
| `cover_customer` | customer / recipient |
| `cover_author` | author (person) |
| `cover_note` | paragraph, line breaks allowed (at most 2000 characters) |
| `classification` | e.g. "Confidential"; also centred in the footer of every page |

The other texts are single-line and at most 200 characters long. Defaults
(e.g. your own company) belong in the base configuration. A value set in the
base cannot be emptied in a profile, only overridden.

Invalid values lead to a message and exit code 1 before any work is done.

## Profiles

A profile is a variant of the configuration (e.g. per customer) in
`~/etc/parameter_report.d/<name>.toml`. It contains only differences from
the base; everything else is inherited. Precedence: built-in defaults < base
< profile. Lists (`hosts`, `folders`, `host_tags`, `host_labels`,
`column_widths`) are replaced, not extended.

- **Name:** a–z, 0–9, `_` and `-`, 1 to 40 characters.
- **Keys:** all keys of the configuration file except `output`,
  `status_file`, `started_by`, `profile`, `mail_to` and the site-wide
  *Advanced settings* (`collect_mode`, `workers`, `chunk_size`,
  `retention_days`); additionally `description` (one line, at most 200
  characters). In the base `profile` and `mail_to` are not allowed.
- Profiles saved on the web page contain, for checkboxes and selection
  fields, only values that differ from the inherited value; filled text and
  number fields are saved even if they equal the base.
- The run records the profile name in the run file and the metadata
  (`profile`); the report list shows it as a badge.

```toml
# ~/etc/parameter_report.d/customer_a.toml
description = "Linux hosts of customer A"
folders = ["customer_a"]
cover_customer = "Customer A"
```

```
0 6 * * 1 . $HOME/.profile; $HOME/local/bin/cmk-parameter-report --profile customer_a >>$HOME/var/log/parameter_report.log 2>&1
```

If a profile is renamed, cron calls using the old name fail.

## Configuration file

`~/etc/parameter_report.toml` is read if it exists (or the file given with
`--config`). Precedence: built-in defaults < file. Unknown keys are an
error. The file (the base) can be edited by hand or on the configuration
page.

```toml
# ~/etc/parameter_report.toml – defaults of this site
workers = 2
chunk_size = 50
retention_days = 90
folders = ["linux"]
changed_only = false
language = "en"
date_format = "YYYY-MM-DD"
column_widths = [34, 22, 14, 16]
highlight_color = "#fde68a"
cover_company = "Example Ltd"
classification = "Confidential"
```

## Cron with a separate file

A cron run with different settings uses a profile (`--profile`, see
*Profiles*) or a separate file, e.g. `~/etc/parameter_report_cron.toml`:

```toml
folders = ["linux"]
cover_title = "Linux hosts"
```

```
0 6 * * 1 . $HOME/.profile; $HOME/local/bin/cmk-parameter-report --config $HOME/etc/parameter_report_cron.toml >>$HOME/var/log/parameter_report.log 2>&1
```

## Content

- Cover page (see *Cover page*): title and details of the recipient, site,
  version, time, number of hosts/services, mode, hosts with errors
- Table of contents (`toc`, on by default; on the web page *Table of
  contents* in the section *Layout*): all hosts alphabetically with page
  number, in columns (landscape 3, portrait 2, A3 more), hosts with errors in
  red; every entry is a link to the first page of the host, and the table
  itself has a bookmark "Contents" (German "Inhaltsverzeichnis", see
  *Language and date format*). About 8 pages for 1,000 hosts.
- Page numbers: every page shows "page N of TOTAL" at the bottom right (as in
  the PDF viewer and the table of contents, so pages can also be found in a
  printout); host pages additionally show "page N of host".
- Per host (PDF bookmark): addresses, tags, labels (with origin), groups,
  parents/cluster nodes, agent mode, data sources, services with parameters
- Appendix "Time periods" if time-specific rules occur (see *Time-specific
  parameters*).
- Highlighted in yellow: set by a rule and different from the plugin
  default. If a rule sets exactly the default, the value is not marked.
- Passwords appear as `***` or as the ID in the password store.

## Status file, metadata, lock

Only one report runs per site at a time (lock
`~/tmp/run/parameter_report.lock`; a second start ends with "A report is
already running (PID …, since …)"). The status file is updated during the
run:

```json
{"state": "rendering", "pid": 1234, "started_at": "2026-10-09T15:00:00",
 "updated_at": "2026-10-09T15:00:12", "started_by": "cmkadmin",
 "phase_progress": [400, 1000],
 "output": "/omd/sites/cmk/var/parameter_report/parameter_report_….pdf",
 "exit_code": null, "message": null}
```

`state`: `starting`, `collecting`, `rendering`, `merging`, `done`, `failed`,
`cancelled`. Next to every PDF there is `<name>.json` with site, settings,
who started it, times, number of hosts/services, hosts with errors and peak
memory.

Runs from the web page additionally write a run file
`parameter_report_<…>.toml` next to PDF and JSON: the complete settings of
the run (base and profile, including `output`, `status_file`, `started_by`);
the run starts with `--config <run file>`. It is deleted together with the
report (*Delete*, `retention_days`); the web page removes run files without
a PDF (failed start) after one day. A run can be repeated with
`cmk-parameter-report --config <run file>`; this overwrites the same PDF. A
run file contains a fixed `output` and `started_by` and must therefore not
be reused as a cron configuration (every run would overwrite the same PDF);
for cron use a separate file without `output`.

`SIGTERM`/`Ctrl-C` aborts cleanly (helper processes ended, temporary files
deleted, status `cancelled`); every other error ends with `failed`. If the
process is killed (`kill -9`, OOM killer), the helper processes die with it
and the next run cleans up the leftovers, but the status file then stays at
`collecting`/`rendering`/`merging`. A caller (GUI) recognises this as
follows: `state` is not `done`/`failed`/`cancelled` and the process `pid` is
no longer running or the lock is free
(`flock -n ~/tmp/run/parameter_report.lock true` succeeds).

### Retention

`retention_days` of the base: after every successful run and every time the
web page is opened, reports older than N days are deleted, no matter which
profile they belong to; 0 = never. A run with `--config` also uses the value
of the base; `retention_days` in such a file is not used for this.

Retention and the clean-up of old temporary directories only affect files
that this report wrote for this site (metadata with `"site"`, or a marker in
the temporary directory); other files in a shared target directory are left
alone.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | report written |
| 1 | no report: not the site user, invalid setting, a report is already running, no hosts, configuration cannot be loaded, error while rendering/writing, cancelled |
| 2 | report written, but at least one host with an error (see cover page) |

## Schedules and mail

The base and every profile can have a schedule and a list of recipients. At
the scheduled time the report is created automatically and sent by mail as a
PDF attachment, without a cron entry. The model is Checkmk's report
scheduler (Ultimate/Pro): the same periods, the same time calculation
(`cmk.utils.schedule`), the same mail path.

```toml
# in the base or a profile
[schedule]
enabled = true
period = "week"          # day | week | month_begin | month_end
day = 0                  # see below
time = "06:00"           # HH:MM
recipients = ["it@customer-a.example", "boss@customer-a.example"]

# only in the base
[mail]
sender = "checkmk@example.com"   # empty: Checkmk's default sender
max_attachment_mb = 10           # 1..100, default 10
```

- **Periods:** `day` (daily; then only `day = 0` is allowed), `week` (`day`
  0 = Monday … 6 = Sunday), `month_begin` (day of the month, `day` 1..28),
  `month_end` (counted from the end of the month, `day` 1..28, 1 = last
  day). The day is limited to 28 so that no month is skipped.
- **Recipients:** at most 20 addresses of at most 254 characters, without
  whitespace and control characters, without `,<>"'()[];:\`, without a
  leading `-`, without empty domain parts; `@` and a domain with a dot are
  required. The same rules apply to `sender`. A schedule without recipients
  is allowed; the report is then created without mail.
- **Not inherited:** `[schedule]` only applies to the file it is in; a
  profile does not inherit the schedule of the base. `[mail]` is only
  allowed in the base (an error in a profile). An invalid section makes the
  file invalid like any other error.

### How scheduling works

A cron job `parameter_report_schedules` in Checkmk's `ui-job-scheduler`
checks all schedules every minute. A schedule is due if its last date is
newer than the last run and at most one hour ago; if the site was stopped at
that time, the run is made up for within this hour. Only one report runs at
a time: if one is active, the check is repeated in the next minute until the
hour is over (then the date is dropped; it is shown as "skipped: another
report was running until the window closed" under *Last run* and in the log,
but only if the entry already has a state or the date is at most 24 hours
ago, so that a freshly saved schedule does not report a date from days ago).
If the file or its `[schedule]` cannot be read, it is skipped (only a
warning in the log, nothing in the state). If the settings of the base or
the profile are invalid, or the start fails, no run starts; the error is
shown once under *Last run* (the date counts as handled, no retry every
minute). Scheduled runs carry `started_by = "scheduler"`. The log of the
scheduler is `~/var/log/ui-job-scheduler/ui-job-scheduler.log`.

The state (last run, last error per schedule) is in
`~/var/parameter_report/schedule_state.json`; renaming a profile moves it,
deleting removes it.

The scheduler loads plugins only at start: after installing or updating the
MKP run `omd restart ui-job-scheduler` (or `omd restart`).

### Mail

- Sent via sendmail as for Checkmk notifications; a working MTA (e.g.
  Postfix) on the host is required.
- One mail to all recipients. Subject `Check Parameter Report: <title>
  (<profile>)`; without a custom `cover_title` only `Check Parameter Report
  (<profile>)`, without a profile without the brackets. The text names the
  site, the time, the number of hosts and services, the hosts with errors
  and the file name; the PDF is attached.
- Invalid `[mail]` settings do not stop the report: the mail is not sent and
  the result reads `mail settings: …`.
- The limit `max_attachment_mb` (default 10 MB) applies to the fully encoded
  mail: base64 makes the PDF one third larger, plus 64 kB for headers and
  text. If the mail is larger than the limit, it is sent without the
  attachment, with a note on where the report is stored on the server. The
  default is just below Postfix's default `message_size_limit` (10,240,000
  bytes); for a higher limit raise `message_size_limit` first. If the MTA
  rejects the mail with the attachment, it is retried once without it
  (result `attached: false`, note "could not be sent as an attachment");
  only if both attempts fail is it an error.
- Sendmail gets at most 300 seconds. The metadata of the report are written
  before sending (the report is visible even if sending hangs) and completed
  with the mail result afterwards.
- The result is in the metadata (`mail`) and in the details on the report
  page. A mail error leaves the report in place and does not change the exit
  code; the message goes to stderr or the log.

### Command line and Run now

```bash
cmk-parameter-report --profile customer_a --mail
```

`--mail` sends to the recipients of the base or the profile; without
recipients the call ends with exit code 1, and with `--config` it is not
allowed (run files carry `mail_to` themselves). *Run now* on the
configuration page and the checkbox on the page *Create report* trigger the
same mail.

## Cron

`~/etc/cron.d/parameter_report` (every Monday 06:00; `retention_days` is in
the configuration file):

```
0 6 * * 1 . $HOME/.profile; $HOME/local/bin/cmk-parameter-report >>$HOME/var/log/parameter_report.log 2>&1
```

Then run `omd reload crontab`. Without cron, use a schedule (see *Schedules
and mail*).

## Measurements

Devcontainer, 1,000 hosts × 50 services: Apple Silicon, Checkmk image amd64
under Rosetta emulation, Docker VM with 10 CPUs, other containers active in
parallel. Result: about 3,000 pages, 8 MB. "At the same time" is the highest
RAM usage of all processes together.

| Variant | Runtime | while collecting | while rendering | at the same time, maximum |
|---|---|---|---|---|
| `workers = 4` | 24.5 s | about 355 MB | about 430 MB (157 + 4 × 69) | about 430 MB |
| **`workers = 2` (default)** | **29.5–30.3 s** | about 355 MB | about 295 MB (157 + 2 × 69) | **about 355 MB** |
| `workers = 1` | 39.8 s | about 355 MB | about 210 MB (renders in the main process) | about 355 MB |
| `collect_mode = "inline"` (2 workers) | 33.7 s | – | about 495 MB (356 + 2 × 70) | about 495 MB |

For comparison, `cmk -D` per host: about 2.4 s per host, i.e. about 40 min
for 1,000 hosts.

Collecting sets the lower limit: Checkmk needs about 300 MB for the loaded
configuration (1,000 hosts), exactly as `cmk -D` does. From 2 workers on,
rendering is no longer the peak; more workers save time and cost about 70 MB
each, and `workers = 1` saves no more RAM. The main process grows with the
number of hosts (about 100 MB of data per 1,000 hosts × 50 services, plus
about 60 MB while merging per 3,000 pages). On a native x86 server the times
are considerably shorter.

## Development

- Code: `plugins/parameter_report/lib/` (only `collector.py` uses Checkmk
  internals)
- Test hosts: `tests/fixtures/create_test_hosts.sh` (1,000 hosts in the
  folder `/spike`, test rules in
  `~/etc/check_mk/conf.d/parameter_report_test_rules.mk`)
- Tests: `pytest` (integration tests with the marker `site` are skipped
  without the test hosts)
