# Check Parameter Report

`cmk-parameter-report` schreibt ein PDF (A4 quer) mit allen Hosts einer
Checkmk-Site und den **effektiven Parametern aller Services**: Adressen,
Tags, Labels, Gruppen, Datenquellen und pro Service die wirksamen
Check-Parameter. Per Regel vom Plugin-Default abweichende Werte sind
gelb hinterlegt.

Die Checkmk-Konfiguration wird nur einmal geladen. Für 1.000 Hosts dauert
ein Lauf rund 30 Sekunden (Devcontainer, emuliert) statt ca. 40 Minuten mit
`cmk -D` pro Host. Alle Varianten und Layout-Werte stehen in einer zentralen
Konfigurationsdatei `~/etc/parameter_report.toml` (Basis) und optionale
Profile `~/etc/parameter_report.d/<name>.toml`, die auf der Webseite
gepflegt werden.

Admins erstellen Reports auch über die Webseite *Setup › Maintenance ›
Check Parameter Report*: eigene Konfigurationsseite für Basis und Profile,
Seite *Create report* (Profil wählen, starten), Fortschritt, Abbrechen,
Download und Löschen. Ein Lauf nutzt immer die gespeicherten Einstellungen
von Basis bzw. Profil. Basis und Profile können einen Zeitplan und Empfänger haben: der
Report entsteht automatisch und geht als PDF per Mail raus (sendmail wie bei
Checkmk-Benachrichtigungen). Programmtexte sind englisch.

Getestet mit Checkmk 2.5.0p15 Ultimate. Benötigt nur Pakete aus der
Site-Python (reportlab, pypdf).

## Nutzung

Als Site-User:

```bash
cmk-parameter-report                      # Einstellungen aus ~/etc/parameter_report.toml
cmk-parameter-report --profile kunde_a    # Basis + ~/etc/parameter_report.d/kunde_a.toml
cmk-parameter-report --config ~/etc/parameter_report_linux.toml -v
```

Alle Einstellungen, Konfigurationsdatei, Statusdatei, Exit-Codes, Cron-Beispiel
und Messwerte: [docs/parameter_report.md](docs/parameter_report.md).

## Installation

Als MKP (Checkmk 2.5.x; ab 2.6 deaktiviert Checkmk das Paket, bis es dort
getestet und `version.usable_until` angehoben ist):

```bash
mkp add parameter_report-<version>.mkp       # z. B. parameter_report-0.1.0.mkp
mkp enable parameter_report <version>
omd reload apache   # lädt die Webseite
omd restart ui-job-scheduler   # Zeitpläne: der Scheduler lädt Plugins nur beim Start
```

Oder in der GUI unter *Setup › Maintenance › Extension packages*.

Das Paket enthält:

| Repo | Site |
|---|---|
| `plugins/parameter_report/lib/` | `~/local/lib/python3/cmk_addons/plugins/parameter_report/lib/` |
| `plugins/parameter_report/gui/` | `~/local/lib/python3/cmk_addons/plugins/parameter_report/gui/` |
| `lib/gui/plugins/wato/parameter_report.py` | `~/local/lib/python3/cmk/gui/plugins/wato/parameter_report.py` (Ladedatei der Webseite) |
| `bin/cmk-parameter-report` | `~/local/bin/cmk-parameter-report` |

reportlab und pypdf sind Teil von Checkmk und daher nicht im Paket.

## Paket bauen

Manifest ist die Datei `package`. Als Site-User mit dem Repo als
Arbeitsverzeichnis (z. B. im Devcontainer):

```bash
ln -sfn "$PWD/package" ~/var/check_mk/packages/parameter_report
mkp package ~/var/check_mk/packages/parameter_report
cp ~/var/check_mk/packages_local/parameter_report-<version>.mkp .
```

Neue Dateien in `plugins/parameter_report/` (`.py`, `.toml`) müssen ins Manifest;
`tests/test_package.py` schlägt sonst fehl. Vor jedem Build `version`
in `package` und `Changelog.md` anpassen.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `plugins/parameter_report/lib/collector.py` | lädt die Konfiguration, einziges Modul mit Checkmk-Interna |
| `plugins/parameter_report/lib/model.py` | Datenklassen, Herkunft der Parameter (Default, Discovery, Regel) |
| `plugins/parameter_report/lib/settings.py` | alle Einstellungen: Standardwerte, Prüfung |
| `plugins/parameter_report/lib/config_file.py` | Konfigurationsdatei lesen, schreiben (atomar, `.bak`) |
| `plugins/parameter_report/lib/profiles.py` | Profile: Namen, Vererbung, Anlegen, Kopieren, Umbenennen, Löschen |
| `plugins/parameter_report/lib/schedule_config.py` | Zeitplan und Mail-Einstellungen: Perioden, Adressen, Prüfung |
| `plugins/parameter_report/lib/schedules.py` | Zustand, Fälligkeit, Start geplanter Läufe (GUI-frei) |
| `plugins/parameter_report/lib/mailer.py` | Mail bauen und über sendmail verschicken |
| `plugins/parameter_report/lib/render_canvas.py` | Host-Seiten, direkt auf die PDF-Seite gezeichnet |
| `plugins/parameter_report/lib/render.py` | Deckblatt, Anhang „Zeiträume“ und gemeinsame Texte |
| `plugins/parameter_report/lib/toc.py` | Inhaltsverzeichnis (Layout, Zeichnen, Link-Positionen) |
| `plugins/parameter_report/lib/i18n.py`, `lib/locales/*.toml` | Texte von PDF und Mail je Sprache (en, de), Datumsformate |
| `plugins/parameter_report/lib/report.py` | paralleles Rendern in Blöcken, Zusammenfügen mit pypdf |
| `plugins/parameter_report/lib/runstate.py` | Sperre, Statusdatei, Metadaten, Aufbewahrung, Abbruch |
| `plugins/parameter_report/lib/cli.py` | Kommandozeile, Ablauf, Exit-Codes |
| `plugins/parameter_report/lib/gui_backend.py` | Logik der Webseite (Formular, Start, Status, Liste) |
| `plugins/parameter_report/gui/pages.py` | Report-Seite (Checkmk-GUI-Interna) |
| `plugins/parameter_report/gui/create_page.py` | Seite *Create report* (Profil wählen, Lauf starten) |
| `plugins/parameter_report/gui/config_page.py` | Konfigurationsseite (Basis und Profile) |
| `plugins/parameter_report/gui/common.py` | gemeinsame Teile beider Seiten |
| `lib/gui/plugins/wato/parameter_report.py` | Ladedatei der Webseite |

Nach einem Checkmk-Update zuerst die Integrationstests laufen lassen
(`pytest -m site`); Änderungen an den Interna zeigen sich in `collector.py`.

## Entwicklung

Tests laufen als Site-User mit der Site-Python:

```bash
pytest                                   # Unit-Tests; Site-Tests ohne Testhosts übersprungen
tests/fixtures/create_test_hosts.sh      # 1.000 Testhosts im Ordner /spike anlegen
pytest -m site                           # Integrationstests gegen die Site
black plugins tests && isort plugins tests && flake8 plugins tests
```

Die Entwicklungsumgebung (Devcontainer mit Checkmk-Site) basiert auf
[checkmk-plugin-template](https://github.com/chexma/checkmk-plugin-template)
und ist nicht Teil dieses Repos.
