# cmk-parameter-report

PDF mit allen Hosts der Site und den effektiven Parametern aller Services.
Lädt die Checkmk-Konfiguration einmal; getestet mit Checkmk 2.5.0p15 Ultimate.
Die Kommandozeile ist die einzige Stelle, die Reports erzeugt; die Webseite
startet sie, liest die Statusdatei und bietet die PDFs an. Alle Einstellungen
stehen in einer Basis-Konfiguration (`~/etc/parameter_report.toml`) und
optional in Profilen (`~/etc/parameter_report.d/<name>.toml`); beides lässt
sich auf der Webseite bearbeiten.

## Webseite

Drei Seiten unter *Setup › Maintenance*: die Report-Seite *Check Parameter
Report* (`parameter_report.py`), die Seite *Create report*
(`parameter_report_create.py`) und die Konfigurationsseite
(`parameter_report_config.py`, Button *Configuration*).

### Report-Seite

- **Berechtigung:** `parameter_report.use`, standardmäßig nur die Rolle
  „admin“ (und davon abgeleitete Rollen); änderbar unter *Setup › Roles &
  permissions*. Andere Benutzer sehen weder Menüeintrag noch Seiten.
- **Create report:** Button oben links, öffnet die Seite *Create report*.
  Läuft gerade ein Report, steht dort stattdessen ein Hinweis.
- **Configuration:** Button, öffnet die Konfigurationsseite.
- **Current run:** Phase, Fortschrittsbalken (aktualisiert alle 3 s),
  Startzeit und Benutzer, Button *Cancel* (sendet SIGTERM, sauberer Abbruch),
  nach Ende Button *Download PDF*. Die farbige Kante zeigt den Zustand
  (gelb läuft oder abgebrochen, grün fertig, rot fehlgeschlagen).
  Wurde ein Lauf hart beendet, erscheint eine Warnung; ein neuer Start ist
  möglich.
- **Reports:** alle Reports dieser Site aus `~/var/parameter_report/`
  (auch per Cron erzeugte), über die volle Breite, neueste zuerst:
  - *Report*: Titel des Deckblatts, Profil als Schild, darunter Zeitpunkt
    und wer gestartet hat („scheduler“ bei geplanten Läufen). Ein Klick auf
    den Titel klappt die Details auf (Einstellungen, Zeiten je Phase,
    Speicher, Mailergebnis, Dateiname). Der Report des letzten Laufs ist
    mit *Latest* markiert.
  - *Scope*: Hosts und Services; „only changed parameters“, wenn der Report
    nur per Regel gesetzte Werte enthält.
  - *Result*: Zustand in Checkmk-Farben. Bei OK steht nichts weiter; sonst
    der Grund („3 host(s) with errors“, „exit code 1“, „mail failed“).
    Darunter Laufzeit (Start bis Ende), Größe und der Mailstatus.
  - Buttons *Download* und *Delete report* (löscht PDF, Metadaten und
    Lauf-Datei).

  Beim Aufruf der Seite werden Reports gelöscht, die älter als
  `retention_days` der Basis sind (siehe *Aufbewahrung*) – so verschwinden
  alte Reports auch, wenn kein neuer Lauf stattfindet.

### Seite Create report

- Auswahl *Profile* (Base = nur die Basis) mit einer Zusammenfassung des
  gewählten Eintrags (Beschreibung, Auswahl der Hosts, Titel, Kunde, Firma,
  Zeitplan, Empfänger) und dem Link *Edit* auf die Konfigurationsseite. Ein
  Wechsel lädt die Seite neu.
- Einstellungen werden hier nicht eingegeben: Der Lauf nutzt die
  gespeicherten Werte von Basis bzw. Profil. Für einen abweichenden Lauf
  (z. B. nur ein Host) ein Profil anlegen. Zielordner und Statusdatei sind
  fest (Standardpfade).
- Kästchen *Send by mail to the profile's recipients* (nur sichtbar, wenn
  Basis bzw. gewähltes Profil Empfänger hat): der Lauf verschickt den Report
  per Mail.
- *Create report* startet den Lauf und führt zurück zur Report-Seite, wo der
  Fortschritt läuft. Läuft schon ein Report, steht statt des Buttons ein
  Hinweis. *Back to reports* führt ohne Start zurück.

### Konfigurationsseite

Seite `parameter_report_config.py`; Link *Back to reports* führt zurück.

- Tabelle: Zeile „Base (all reports)“ und je eine Zeile pro Profil mit
  *Edit*, *Copy*, *Rename* und *Delete*; *New profile* legt ein Profil an.
  Ein defektes Profil erscheint in der Tabelle als ungültig.
- Darunter das Formular des gewählten Eintrags (*Hosts to include*, *Report
  content*, *Layout*, *Cover page*, *Schedule and mail*, bei der Basis
  außerdem *Mail settings* und ganz unten *Advanced settings*). *Advanced
  settings* gibt es nur bei der Basis: sie gelten für die ganze Site.
  Profile haben zusätzlich *Description* (eine Zeile, höchstens 200 Zeichen).
  In einem Profil zeigen die Platzhalter den geerbten Wert aus Basis bzw.
  eingebautem Standard.
- *Save* schreibt nur diese Datei neu: Kommentare bleiben nicht erhalten,
  die vorige Fassung liegt als `<datei>.bak` daneben. Leere Felder werden
  nicht gespeichert; Kästchen und Auswahlfelder nur, wenn sie vom geerbten
  Wert abweichen; ausgefüllte Text- und Zahlenfelder werden gespeichert.
  Von Hand gesetzte Schlüssel `output`, `status_file` und `started_by` der
  Basis bleiben beim Speichern erhalten.
- Wurde die Datei inzwischen geändert (Prüfung je Datei), kommt eine
  Meldung „… changed meanwhile“ und nichts wird geschrieben. Ist die Datei
  ungültig, erscheint eine Warnung und das Formular ist leer.
- Das Speichern der Basis wird abgelehnt, wenn dadurch ein Profil ungültig
  würde.
- Die Tabelle hat die Spalten *Schedule* (z. B. „weekly, Mon 06:00“ oder „–“),
  *Next run* (nur bei aktivem Zeitplan) und *Last run* (Zeitpunkt und
  Ergebnis: OK, hosts with errors, failed, mail failed; bei ungültigen
  Einstellungen, gescheitertem Start oder übersprungenem Termin der Fehler).
  Das Ergebnis stammt vom neuesten Scheduler-Report des Eintrags, aber nur,
  wenn dieser höchstens eine Minute vor dem letzten Lauf oder später
  gestartet ist; sonst steht „running“ (ein Report läuft gerade) oder
  „no report (see ~/var/log/parameter_report.log)“, nie das Ergebnis eines
  älteren Laufs. Ist die Datei eines Profils oder ihr `[schedule]` nicht
  lesbar, steht in dessen Zeile bei der Beschreibung „invalid – edit to
  repair“ und in den Zeitplan-Spalten „–“. *Run now* startet sofort einen Lauf wie der
  Scheduler, mit Mail; der nächste geplante Termin bleibt unverändert.
  Die Schaltfläche gibt es für Einträge mit Empfängern oder aktivem Zeitplan.
- Abschnitt *Schedule and mail* (zugeklappt): *Enabled*, *Period*, *Day*,
  *Time*, *Recipients* (durch Komma getrennt) und *Test mail*: der Button
  *Send test mail* schickt eine kurze Mail ohne Report an die gespeicherten
  Empfänger dieses Eintrags (Absender aus *Mail settings*, Sprache des
  Eintrags); ungespeicherte Änderungen im Formular zählen nicht, gespeichert
  wird dabei nichts. Oben auf der Seite steht danach „Test mail sent to the
  saved recipients.“ oder der Fehler von sendmail. Ohne gespeicherte
  Empfänger oder ohne sendmail steht statt des Buttons ein Hinweis. Nur in
  der Basis zusätzlich
  *Mail settings* mit *Sender address* und *Maximum attachment size (MB)*.
  Warnungen: „sendmail is missing; …“, wenn kein sendmail gefunden wird, und
  „Scheduler not available: …“, wenn der Cronjob nicht registriert ist.
  Ist die Datei defekt, fehlen die Zeitplan-Felder, damit Speichern den
  Zeitplan nicht löscht.
- *Delete* entfernt das Profil, `<name>.toml.bak` bleibt liegen. *Rename*
  bricht Cron-Aufrufe mit dem alten Namen.

### Allgemein

- Gestartet wird im Hintergrund mit `setsid`, unabhängig vom Webserver; die
  Ausgaben stehen in `~/var/log/parameter_report.log`.
- Nach Installation des MKP über die Kommandozeile: `omd reload apache`
  (über die GUI installiert lädt Checkmk selbst neu).
- Checkmk 2.5 hat für eigene Seiten keine stabile Schnittstelle. Die Seite
  wird über `~/local/lib/python3/cmk/gui/plugins/wato/parameter_report.py`
  geladen; *Analyze configuration* zeigt dafür eine Warnung „deprecated“.
  Scheitert das Laden nach einem Update, steht der Fehler in
  `~/var/log/web.log`, die übrige GUI läuft weiter.
- Der Report enthält nur Hosts der Site, auf der er läuft (bei verteiltem
  Monitoring also nicht die Hosts der Remote-Sites).

Alle Programmtexte (Seite, Meldungen, Hilfe, PDF) sind englisch.

## Aufruf

Als Site-User:

```
cmk-parameter-report [--profile NAME | --config DATEI] [-v]
```

Ohne Schalter wird `~/etc/parameter_report.toml` gelesen, mit `--config`
genau `DATEI`, mit `--profile` die Basis plus das Profil (siehe *Profile*).
`--profile` und `--config` zusammen sind ein Fehler; ein unbekanntes Profil
endet mit „Profile NAME not found. Available: …“ (Exit-Code 1). Ein ungültiger
Name, auch `--profile ""`, endet mit der Namensregel „Profile names: a-z, 0-9, _
and -, at most 40 characters.“. Fehlt die Basis, gelten die
eingebauten Standards (alle Hosts, Ausgabe nach `~/var/parameter_report/`).
Mit `--config` muss die Datei existieren und gültig sein. `-v` zeigt Zeiten
je Phase und Spitzenspeicher je Prozess. Weitere Schalter gibt es nicht;
alle Einstellungen stehen in den Tabellen unten (Spalte *Einstellung*) und
sind Schlüssel der Konfigurationsdatei. Varianten (z. B. für Cron) sind
Profile.

### Auswahl

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `hosts` | alle | nur diese Hosts |
| `folders` | – | Setup-Ordner inkl. Unterordner, z. B. `linux/web`; mehrfach: ODER |
| `host_tags` | – | mehrfach: alle müssen passen |
| `host_labels` | – | mehrfach: alle müssen passen |
| `changed_only` | aus | nur per Regel gesetzte Werte; Services ohne solche Werte entfallen |

Alle Kriterien gelten zusammen. Hosts außerhalb der Auswahl, die per
`hosts` genannt werden, erscheinen als „Unbekannter Host“.

### Sprache und Datumsformat

Auf der Webseite im Abschnitt *Report content*; in der Basis und pro Profil
einstellbar, unabhängig voneinander.

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `language` | `en` | Sprache der festen Texte in PDF und Mail: `en`, `de` |
| `date_format` | `YYYY-MM-DD` | Zeitpunkte in PDF und Mail: `YYYY-MM-DD` (2026-10-10 15:14), `DD.MM.YYYY` (10.10.2026 15:14), `DD/MM/YYYY` (10/10/2026 15:14), `MM/DD/YYYY` (10/10/2026 03:14 PM) |

Übersetzt werden alle festen Texte: Beschriftungen des Deckblatts (z. B.
*Classification* → *Klassifizierung*), Spaltenköpfe, Host-Felder,
Inhaltsverzeichnis, Fußzeilen, Leer-Hinweise, Standardtitel sowie Betreff
und Text der Mail. Nicht übersetzt werden Daten aus Checkmk (Service-Namen,
Parameternamen und -werte, Tags, Labels), selbst eingetragene Texte (Titel,
Firma, Klassifizierungswert …) und die Weboberfläche.

Die Texte stehen in `lib/locales/<sprache>.toml` (im Plugin-Verzeichnis).
Eine weitere Sprache: `en.toml` kopieren, z. B. als `fr.toml`, die Werte
übersetzen (Schlüssel und `{platzhalter}` bleiben), die Datei im Manifest
`package` eintragen; sie erscheint dann automatisch in der Auswahl. Ein Test
prüft, dass jede Sprache genau dieselben Schlüssel und Platzhalter hat.

### Zeitabhängige Parameter

Regeln mit zeitabhängiger Spezifikation (Standardwert plus Werte je
Zeitraum) wertet der Report wie Checkmk aus: Von den gerade aktiven
Einträgen gilt der erste, Schlüssel für Schlüssel.

- Zeiträume, die immer aktiv sind, zählen als dauerhaft eingeschaltet: der
  eingebaute `24X7` sowie eigene Zeiträume, die an allen sieben Tagen
  00:00–24:00 abdecken, nichts ausschließen und keine kürzeren Ausnahmen
  haben. Ihre Werte stehen in der Hauptzeile, mit dem Präfix
  „[Always active: Always (24X7)]“ (deutsch „Immer aktiv“).
- Je weiterem Zeitraum eine Zeile „» Time period Alias (ID): …“ mit nur den
  Werten, die sich von der Hauptzeile unterscheiden; ohne Unterschied
  entfällt die Zeile. Bei `changed_only` bleiben alle diese Abweichungen
  stehen.
- Anhang „Time periods“ (deutsch „Zeiträume“) nach dem letzten Host: die
  verwendeten Zeiträume und die, die sie ausschließen, mit Wochentagen,
  Uhrzeiten, Ausnahmen (`YYYY-MM-DD`) und ausgeschlossenen Zeiträumen; mit
  Lesezeichen und letztem Eintrag im Inhaltsverzeichnis. Ein Zeitraum, den
  eine Regel nennt, der aber nicht (mehr) existiert, steht dort als
  „not defined, never active“.
- Nicht ausgewertet: ob sich zwei eigene Zeiträume zeitlich überschneiden.

### Ablauf

`collect_mode`, `workers`, `chunk_size` und `retention_days`
gelten für die ganze Site und stehen nur in der Basis; in einem Profil sind
sie ein Fehler („… is only allowed in the base configuration“).

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `output` | `~/var/parameter_report/parameter_report_<Datum>_<Zeit>_<ID>.pdf` | Ziel-PDF |
| `collect_mode` | `subprocess` | Konfiguration in eigenem Prozess laden (`inline`: im Hauptprozess, schneller, mehr Speicher) |
| `workers` | 2 (1 bei weniger als 4 CPUs) | Render-Prozesse; mehr = schneller, aber mehr RAM |
| `chunk_size` | 50 | Hosts je Render-Block |
| `retention_days` | 0 | Reports löschen, die älter als N Tage sind (0 = nie, höchstens 3650) |
| `status_file` | `~/var/parameter_report/status.json` | Status des Laufs |
| `started_by` | Site-User | für Status und Metadaten |

### Layout

| Einstellung | Standard |
|---|---|
| `page_size` | `A4` |
| `portrait` | aus (quer) |
| `margin_mm` | 12 |
| `font_size` | 6.5 |
| `line_height` | 8.0 (zwischen 1× und 3× `font_size`) |
| `column_widths` | `34,22,14,16` (Zeichen für Service, Plugin, Item, Servicegruppen; Parameter erhält den Rest, mindestens 20) |
| `min_split_lines` | 2 |
| `max_param_chars` | 20000 |
| `max_cover_error_chars` | 500 (höchstens 5000) |
| `highlight_color` | `#fde68a` |
| `toc` | an (Inhaltsverzeichnis, siehe *Inhalt*) |

### Deckblatt

Der Report beginnt mit einem Deckblatt: Titel, darunter der Kunde, dann eine
Tabelle mit Firma, Ersteller, Vertraulichkeit, Erstellungszeitpunkt, Site,
Checkmk-Version, Anzahl Hosts/Services und Modus, darunter Bemerkung,
Legende und die Hosts mit Fehlern. Nicht gesetzte Angaben entfallen; ohne
diese Werte bleibt nur die Übersicht. Viele Hosts mit Fehlern setzen das
Deckblatt auf weiteren Seiten fort. Der Titel ist auch der PDF-Titel
(Metadaten).

| Einstellung | Bedeutung |
|---|---|
| `cover_title` | Titel; leer = „Check Parameter Report“ |
| `cover_company` | Firma, die den Report erstellt |
| `cover_customer` | Kunde / Empfänger |
| `cover_author` | Ersteller (Person) |
| `cover_note` | Absatz, Zeilenumbrüche erlaubt (höchstens 2000 Zeichen) |
| `classification` | z. B. „Confidential“; zusätzlich mittig in der Fußzeile jeder Seite |

Die übrigen Texte sind einzeilig und höchstens 200 Zeichen lang. Standards
(z. B. die eigene Firma) gehören in die zentrale Konfiguration. Ein dort
gesetzter Standard lässt sich auf der Webseite nicht für einen
einzelnen Lauf leeren, nur überschreiben.

Ungültige Werte führen vor jeder Arbeit zu einer Meldung und Exit-Code 1.

## Profile

Ein Profil ist eine Variante der Konfiguration (z. B. je Kunde) in
`~/etc/parameter_report.d/<name>.toml`. Es enthält nur Abweichungen von der
Basis; alles andere wird geerbt. Vorrang: eingebaute Standards < Basis <
Profil. Listen
(`hosts`, `folders`, `host_tags`, `host_labels`, `column_widths`) werden
ersetzt, nicht ergänzt.

- **Name:** a–z, 0–9, `_` und `-`, 1 bis 40 Zeichen.
- **Schlüssel:** alle der Konfigurationsdatei außer `output`, `status_file`,
  `started_by`, `profile`, `mail_to` und den site-weiten *Advanced settings*
  (`collect_mode`, `workers`, `chunk_size`, `retention_days`);
  zusätzlich `description` (eine Zeile, höchstens 200 Zeichen). In der Basis
  sind `profile` und `mail_to` nicht erlaubt.
- Auf der Webseite gespeicherte Profile enthalten bei Kästchen und
  Auswahlfeldern nur Werte, die vom geerbten Wert abweichen; ausgefüllte
  Text- und Zahlenfelder werden auch gespeichert, wenn sie der Basis gleichen.
- Der Lauf trägt den Profilnamen in Lauf-Datei und Metadaten (`profile`);
  die Reportliste zeigt ihn in der Spalte *Profile*.

```toml
# ~/etc/parameter_report.d/kunde_a.toml
description = "Linux-Hosts von Kunde A"
folders = ["kunde_a"]
cover_customer = "Kunde A"
```

```
0 6 * * 1 . $HOME/.profile; $HOME/local/bin/cmk-parameter-report --profile kunde_a >>$HOME/var/log/parameter_report.log 2>&1
```

Wird ein Profil umbenannt, schlagen Cron-Aufrufe mit dem alten Namen fehl.

## Konfigurationsdatei

`~/etc/parameter_report.toml` wird gelesen, wenn sie existiert (oder die mit
`--config` genannte Datei). Vorrang: eingebaute Standardwerte < Datei.
Unbekannte Schlüssel sind ein Fehler. Die Datei (die Basis) lässt sich von Hand oder auf
der Konfigurationsseite bearbeiten.

```toml
# ~/etc/parameter_report.toml – Standardwerte dieser Site
workers = 2
chunk_size = 50
retention_days = 90
folders = ["linux"]
changed_only = false
language = "de"
date_format = "DD.MM.YYYY"
column_widths = [34, 22, 14, 16]
highlight_color = "#fde68a"
cover_company = "Example GmbH"
classification = "Confidential"
```

## Cron mit eigener Datei

Ein Cron-Lauf mit abweichenden Einstellungen nutzt ein Profil (`--profile`,
siehe *Profile*) oder eine eigene Datei, z. B.
`~/etc/parameter_report_cron.toml`:

```toml
folders = ["linux"]
cover_title = "Linux-Hosts"
```

```
0 6 * * 1 . $HOME/.profile; $HOME/local/bin/cmk-parameter-report --config $HOME/etc/parameter_report_cron.toml >>$HOME/var/log/parameter_report.log 2>&1
```

## Inhalt

- Deckblatt (siehe *Deckblatt*): Titel und Angaben zum Empfänger, Site,
  Version, Zeitpunkt, Anzahl Hosts/Services, Modus, Hosts mit Fehlern
- Inhaltsverzeichnis (`toc`, Standard an; auf der Webseite *Table of
  contents* im Abschnitt *Layout*): alle Hosts alphabetisch mit Seitenzahl,
  mehrspaltig (quer 3, hoch 2 Spalten, A3 mehr), Hosts mit Fehlern rot;
  jeder Eintrag ist ein Link auf die erste Seite des Hosts, das Verzeichnis
  hat selbst ein Lesezeichen „Contents“ (deutsch „Inhaltsverzeichnis“, siehe
  *Sprache und Datumsformat*). Bei 1.000 Hosts etwa 8 Seiten.
- Seitenzahlen: jede Seite trägt unten rechts „page N of GESAMT“ (wie im
  PDF-Betrachter und im Inhaltsverzeichnis, also auch ausgedruckt
  auffindbar); Host-Seiten zusätzlich „page N of host“.
- Pro Host (PDF-Lesezeichen): Adressen, Tags, Labels (mit Herkunft), Gruppen,
  Parents/Cluster-Knoten, Agent-Modus, Datenquellen, Services mit Parametern
- Anhang „Time periods“, wenn zeitabhängige Regeln vorkommen (siehe
  *Zeitabhängige Parameter*).
- Gelb hinterlegt: per Regel gesetzt und abweichend vom Plugin-Default.
  Setzt eine Regel exakt den Default, ist der Wert nicht markiert.
- Passwörter erscheinen als `***` bzw. als ID im Passwortspeicher.

## Statusdatei, Metadaten, Sperre

Es läuft immer nur ein Report je Site (Sperre `~/tmp/run/parameter_report.lock`;
ein zweiter Start endet mit „A report is already running (PID …, since …)“).
Die Statusdatei wird während des Laufs aktualisiert:

```json
{"state": "rendering", "pid": 1234, "started_at": "2026-10-09T15:00:00",
 "updated_at": "2026-10-09T15:00:12", "started_by": "cmkadmin",
 "phase_progress": [400, 1000],
 "output": "/omd/sites/cmk/var/parameter_report/parameter_report_….pdf",
 "exit_code": null, "message": null}
```

`state`: `starting`, `collecting`, `rendering`, `merging`, `done`, `failed`,
`cancelled`. Neben jedem PDF liegt `<name>.json` mit Site, Einstellungen, Startendem,
Zeiten, Host-/Service-Zahl, Hosts mit Fehlern und Spitzenspeicher.

Läufe der Webseite schreiben zusätzlich eine Lauf-Datei
`parameter_report_<…>.toml` neben PDF und JSON: die vollständigen
Einstellungen des Laufs (Basis und Profil,
inkl. `output`, `status_file`, `started_by`); der Lauf startet mit
`--config <Lauf-Datei>`. Sie wird mit dem Report gelöscht (*Delete*,
`retention_days`); Lauf-Dateien ohne PDF (fehlgeschlagener Start) entfernt
die Webseite nach einem Tag. Ein Lauf lässt sich mit
`cmk-parameter-report --config <Lauf-Datei>` wiederholen; das überschreibt
dasselbe PDF. Eine Lauf-Datei enthält ein festes `output` und `started_by`
und darf deshalb nicht als Cron-Konfiguration wiederverwendet werden (jeder
Lauf überschriebe dasselbe PDF); für Cron eine eigene Datei ohne `output`
verwenden.

`SIGTERM`/`Strg-C` bricht sauber ab (Hilfsprozesse beendet, Temp-Dateien
gelöscht, Status `cancelled`); jeder andere Fehler endet mit `failed`.
Wird der Prozess hart beendet (`kill -9`, OOM-Killer), sterben die
Hilfsprozesse mit, Reste räumt der nächste Lauf auf – die Statusdatei bleibt
dann aber in `collecting`/`rendering`/`merging` stehen. Ein Aufrufer (GUI)
erkennt das so: `state` ist nicht `done`/`failed`/`cancelled` und der
Prozess `pid` läuft nicht mehr bzw. die Sperre ist frei
(`flock -n ~/tmp/run/parameter_report.lock true` gelingt).

Aufbewahrung (`retention_days` der Basis: nach jedem erfolgreichen Lauf und
bei jedem Aufruf der Webseite werden Reports gelöscht, die älter als N Tage
sind, gleich zu welchem Profil sie gehören; 0 = nie; auch ein Lauf mit
`--config` nutzt den Wert der Basis, `retention_days` in einer solchen Datei
wird dafür nicht verwendet) und das Aufräumen
alter Temp-Verzeichnisse betreffen
nur Dateien, die dieser Report für diese Site geschrieben hat (Metadaten mit
`"site"` bzw. Markierung im Temp-Verzeichnis); fremde Dateien in einem
gemeinsamen Zielverzeichnis bleiben unberührt.

## Exit-Codes

| Code | Bedeutung |
|---|---|
| 0 | Report geschrieben |
| 1 | kein Report: kein Site-User, ungültige Einstellung, Report läuft bereits, keine Hosts, Konfiguration nicht ladbar, Fehler beim Rendern/Schreiben, abgebrochen |
| 2 | Report geschrieben, aber mindestens ein Host mit Fehler (siehe Deckblatt) |

## Zeitpläne und Mailversand

Basis und jedes Profil können einen Zeitplan und eine Empfängerliste haben.
Zum geplanten Zeitpunkt entsteht der Report automatisch und wird als
PDF-Anhang per Mail verschickt, ohne Cron-Eintrag. Vorbild ist Checkmks
Report Scheduler (Ultimate/Pro): gleiche Perioden, gleiche Zeitberechnung
(`cmk.utils.schedule`), gleicher Mailweg.

```toml
# in der Basis oder einem Profil
[schedule]
enabled = true
period = "week"          # day | week | month_begin | month_end
day = 0                  # siehe unten
time = "06:00"           # HH:MM
recipients = ["it@kunde-a.de", "chef@kunde-a.de"]

# nur in der Basis
[mail]
sender = "checkmk@example.com"   # leer: Standardabsender von Checkmk
max_attachment_mb = 10           # 1..100, Standard 10
```

- **Perioden:** `day` (täglich, dann ist nur `day = 0` erlaubt), `week` (`day` 0 =
  Montag … 6 = Sonntag), `month_begin` (Tag des Monats, `day` 1..28),
  `month_end` (vom Monatsende gezählt, `day` 1..28, 1 = letzter Tag). Der
  Tag ist auf 28 begrenzt, damit kein Monat übersprungen wird.
- **Empfänger:** höchstens 20 Adressen mit höchstens 254 Zeichen, ohne
  Leer- und Steuerzeichen, ohne `,<>"'()[];:\`, ohne führendes `-`, ohne
  leere Domain-Teile; nötig sind `@` und eine Domain mit Punkt. Für
  `sender` gelten dieselben Regeln. Ein Zeitplan ohne Empfänger ist erlaubt; der Report
  entsteht dann ohne Mail.
- **Nicht vererbt:** `[schedule]` gilt nur für die Datei, in der es steht;
  ein Profil erbt den Zeitplan der Basis nicht. `[mail]` ist nur in der
  Basis erlaubt (im Profil ein Fehler). Ein ungültiger Abschnitt macht die
  Datei ungültig wie jeder andere Fehler.

### Ablauf

Ein Cronjob `parameter_report_schedules` im `ui-job-scheduler` von Checkmk
prüft jede Minute alle Zeitpläne. Ein Zeitplan ist fällig, wenn sein letzter
Termin neuer ist als der letzte Lauf und höchstens eine Stunde zurückliegt;
war die Site zum Termin gestoppt, wird innerhalb dieser Stunde nachgeholt.
Es läuft immer nur ein Report: ist einer aktiv, wird in der nächsten Minute
neu geprüft, bis die Stunde um ist (dann entfällt der Termin; er steht als
„skipped: another report was running until the window closed“ unter *Last
run* und im Log, aber nur, wenn der Eintrag schon einen Zustand hat oder der
Termin höchstens 24 Stunden zurückliegt, damit ein frisch gespeicherter
Zeitplan keinen Termin von vor Tagen meldet). Ist die Datei oder
ihr `[schedule]` nicht lesbar, wird sie übersprungen (nur eine Warnung im Log,
nichts im Zustand). Sind die Einstellungen von Basis bzw. Profil ungültig oder
scheitert der Start, startet kein Lauf; der Fehler steht einmal unter *Last
run* (der Termin gilt als erledigt, kein Neuversuch jede Minute). Geplante Läufe tragen `started_by = "scheduler"`. Das Log des Schedulers
ist `~/var/log/ui-job-scheduler/ui-job-scheduler.log`.

Der Zustand (letzter Lauf, letzter Fehler je Zeitplan) liegt in
`~/var/parameter_report/schedule_state.json`; Umbenennen eines Profils
überträgt ihn, Löschen entfernt ihn.

Der Scheduler lädt Plugins nur beim Start: nach Installation oder Update des
MKP `omd restart ui-job-scheduler` (oder `omd restart`).

### Mailversand

- Versand über sendmail wie bei Checkmk-Benachrichtigungen; ein
  funktionierender MTA (z. B. Postfix) auf dem Host ist Voraussetzung.
- Eine Mail an alle Empfänger. Betreff `Check Parameter Report: <Titel>
  (<Profil>)`; ohne eigenen `cover_title` nur `Check Parameter Report
  (<Profil>)`, ohne Profil ohne Klammer. Der Text nennt Site, Zeitpunkt, Anzahl Hosts und Services, Hosts
  mit Fehlern und den Dateinamen; das PDF hängt an.
- Ungültige `[mail]`-Einstellungen stoppen den Report nicht: die Mail wird
  nicht verschickt, das Ergebnis lautet `mail settings: …`.
- Das Limit `max_attachment_mb` (Standard 10 MB) gilt für die fertig codierte
  Mail: base64 macht das PDF um ein Drittel größer, dazu kommen 64 kB für
  Kopfzeilen und Text. Ist die Mail so größer als das Limit, geht sie ohne
  Anhang mit dem Hinweis auf den Speicherort auf dem Server. Der Standard
  liegt knapp unter dem Postfix-Standard `message_size_limit` (10.240.000
  Bytes); bei einem höheren Limit zuerst `message_size_limit` anheben.
  Lehnt der MTA die Mail mit Anhang ab, wird einmal ohne Anhang wiederholt
  (Ergebnis `attached: false`, Hinweis „could not be sent as an attachment“);
  nur wenn beide Versuche scheitern, ist es ein Fehler.
- Sendmail bekommt höchstens 300 Sekunden. Die Metadaten des Reports werden vor
  dem Versand geschrieben (der Report ist sichtbar, auch wenn der Versand
  hängt) und danach um das Mailergebnis ergänzt.
- Das Ergebnis steht in den Metadaten (`mail`) und in den *Details* der
  Report-Seite. Ein Mailfehler lässt den Report bestehen und ändert den
  Exit-Code nicht; die Meldung steht auf stderr bzw. im Log.

### Kommandozeile und Run now

```bash
cmk-parameter-report --profile kunde_a --mail
```

`--mail` verschickt an die Empfänger der Basis bzw. des Profils; ohne
Empfänger endet der Aufruf mit Exit-Code 1, mit `--config` ist er nicht
erlaubt (Lauf-Dateien tragen `mail_to` selbst). *Run now* auf der
Konfigurationsseite und das Kästchen in *New report* lösen denselben
Mailversand aus.

## Zeitgesteuert

`~/etc/cron.d/parameter_report` (jeden Montag 06:00; `retention_days` steht
in der Konfigurationsdatei):

```
0 6 * * 1 . $HOME/.profile; $HOME/local/bin/cmk-parameter-report >>$HOME/var/log/parameter_report.log 2>&1
```

Danach `omd reload crontab`. Ohne Cron geht es mit einem Zeitplan
(siehe *Zeitpläne und Mailversand*).

## Messwerte (Devcontainer, 1.000 Hosts × 50 Services)

Apple Silicon, Checkmk-Image amd64 unter Rosetta-Emulation, Docker-VM mit
10 CPUs, parallel weitere Container aktiv. Ergebnis: ca. 3.000 Seiten, 8 MB.
„Gleichzeitig“ ist der höchste RAM-Bedarf aller Prozesse zusammen.

| Variante | Laufzeit | beim Sammeln | beim Rendern | gleichzeitig maximal |
|---|---|---|---|---|
| `workers = 4` | 24,5 s | ca. 355 MB | ca. 430 MB (157 + 4 × 69) | ca. 430 MB |
| **`workers = 2` (Standard)** | **29,5–30,3 s** | ca. 355 MB | ca. 295 MB (157 + 2 × 69) | **ca. 355 MB** |
| `workers = 1` | 39,8 s | ca. 355 MB | ca. 210 MB (rendert im Hauptprozess) | ca. 355 MB |
| `collect_mode = "inline"` (2 Worker) | 33,7 s | – | ca. 495 MB (356 + 2 × 70) | ca. 495 MB |

Zum Vergleich der alte Ablauf (`cmk -D` pro Host): ca. 2,4 s je Host,
also ca. 40 min für 1.000 Hosts.

Die Untergrenze setzt das Sammeln: Checkmk braucht für die geladene
Konfiguration ca. 300 MB (1.000 Hosts), genau wie `cmk -D`. Ab 2 Workern ist
das Rendern nicht mehr die Spitze; mehr Worker sparen Zeit und kosten je
ca. 70 MB, `workers = 1` spart keinen RAM mehr. Der Hauptprozess wächst mit
der Hostzahl (ca. 100 MB Daten je 1.000 Hosts × 50 Services, plus ca. 60 MB
beim Zusammenfügen je 3.000 Seiten). Auf einem nativen x86-Server sind die
Zeiten deutlich kürzer.

## Entwicklung

- Code: `plugins/parameter_report/lib/` (nur `collector.py` nutzt Checkmk-Interna)
- Testhosts: `tests/fixtures/create_test_hosts.sh` (1.000 Hosts im Ordner `/spike`,
  Testregeln in `~/etc/check_mk/conf.d/parameter_report_test_rules.mk`)
- Tests: `pytest` (Integrationstests mit Marker `site` werden ohne Testhosts übersprungen)
