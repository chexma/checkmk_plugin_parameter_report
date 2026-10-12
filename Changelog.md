# Changelog

## [0.1.0] - 2026-10-12

Erste veröffentlichte Version.

- `cmk-parameter-report`: PDF (A4 quer) mit allen Hosts einer Site und den
  effektiven Parametern aller Services; die Checkmk-Konfiguration wird nur
  einmal geladen, die Seiten entstehen parallel. Per Regel vom
  Plugin-Default abweichende Werte sind hervorgehoben, Passwörter geschwärzt.
- Deckblatt (Titel, Kunde, Firma, Ersteller, Vertraulichkeit, Bemerkung),
  anklickbares Inhaltsverzeichnis, Lesezeichen je Host, durchgehende
  Seitenzahlen.
- Hostauswahl nach Setup-Ordner, Host-Tag und Host-Label; Modus *Only
  changed parameters* (`changed_only`).
- Zeitabhängige Parameter: immer aktive Zeiträume zählen als eingeschaltet,
  Zeitraum-Zeilen zeigen nur Abweichungen; Anhang „Time periods“ mit den
  Definitionen der verwendeten Zeiträume.
- Konfiguration in `~/etc/parameter_report.toml` (Basis) und Profilen in
  `~/etc/parameter_report.d/<name>.toml`, die nur Abweichungen enthalten.
- Webseiten unter *Setup › Maintenance › Check Parameter Report* für Admins
  (Berechtigung `parameter_report.use`): Reports erstellen, Fortschritt,
  Abbrechen, Download, Löschen; Konfigurationsseite für Basis und Profile.
- Zeitpläne je Basis/Profil (täglich, wöchentlich, monatlich) im
  `ui-job-scheduler` und Mailversand des PDFs über sendmail; *Run now* und
  *Send test mail*; `--mail` auf der Kommandozeile.
- Sprache (`en`, `de`) und Datumsformat von PDF und Mail einstellbar.
- Sperre (ein Lauf je Site), Statusdatei, Metadaten je PDF, Aufbewahrung
  über `retention_days`.
- Als MKP paketiert (Checkmk 2.5.0 bis vor 2.6.0).
