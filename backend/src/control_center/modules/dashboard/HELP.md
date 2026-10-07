# Übersicht (Dashboard)

Die Startseite zeigt in Echtzeit, was auf dem AI-Rechner passiert: geladene Modelle, Grafikkarte,
Arbeitsspeicher, Dienste – und warnt, bevor etwas abstürzt.

## Bedienung

### Statuszeile

Ganz oben: **Live** (grüner Punkt) heißt, neue Werte kommen per Live-Verbindung, normalerweise alle
2 Sekunden. **Veraltet** erscheint, wenn 10 Sekunden lang nichts ankam – die Zahlen auf dem Bildschirm
sind dann alt. Daneben stehen der beobachtete Rechner (Knoten) und die Uhrzeit des letzten Messwerts.

### Warnungen

Warnungen stehen über allem anderen, die kritischen zuerst. Jede trägt ein Symbol und ein Stufenwort,
die Farbe ist nie das einzige Signal.

| Warnung | Was tun |
|---|---|
| **Kritisch: Teil-Offload** | Ein Modell liegt teils auf der GPU, teils auf der CPU. Auf dieser Karte (Blackwell) kann das den Runner zum Absturz bringen. Modell mit kleinerem Kontext (`num_ctx`) laden oder andere Modelle entladen. |
| Läuft komplett auf der CPU | Kein Absturzrisiko, aber sehr langsam. Meist zu großer Kontext. |
| Wenig VRAM frei | Das nächste Modell passt eventuell nicht mehr ganz auf die Karte. |
| GPU-Temperatur / GPU drosselt | Lüftung prüfen; bei „Leistungslimit“ ist das unter Volllast normal. |
| Arbeitsspeicher / Laufwerk knapp | Platz schaffen – Modelle brauchen viel davon. |
| Dienst nicht erreichbar | MCP-Server, OpenWebUI oder Qdrant antworten nicht. |
| Ollama offline | Ollama läuft nicht oder ist nicht erreichbar. |

### Kacheln

**GPU-Auslastung**, **VRAM** (belegt und frei), **Temperatur & Leistung** (mit Lüfter und eventuellen
Drosselgründen) und **Ollama** (online/offline, Anzahl geladener Modelle, Version, Antwortzeit).

### Geladene Modelle

Pro Modell: Name und Eckdaten, der **GPU/CPU-Balken**, Speicherbedarf, Kontextlänge und wann es
entladen wird. Der Balken ist blau, wenn das Modell komplett auf der Grafikkarte liegt; ein roter
Anteil heißt Teil-Offload (siehe Warnungen), ein gelber komplett CPU. „angepinnt“ bedeutet: wird nie
automatisch entladen.

### Ereignisse und Dienste

Die Ereignisliste hält fest, was passiert ist: Modell geladen oder entladen, Teil-Offload begonnen oder
beendet, Ollama weg, wieder da oder neu gestartet (kürzer als 30 Sekunden weg), aktualisiert oder
abgestürzt. „Ältere laden“ blättert zurück. Ganz unten steht, welche Modelle **nur gezählt** werden –
das Embedding-Modell wird bei jeder RAG-Anfrage geladen und würde die Liste sonst fluten.

Die **Absturz-Erkennung** liest das Ollama-Log und meldet Abstürze des Runners. Sie arbeitet nur, wenn
das Control Center auf demselben Rechner läuft wie Ollama; sonst steht dort „aus“ mit dem Grund.

Rechts daneben: die **Dienste** mit Antwortzeit oder Fehlertext.

### Verlauf

Diagramme für GPU-Last, VRAM, Temperatur, Leistung, CPU und Arbeitsspeicher. Oben rechts den Zeitraum
wählen: **1 h** zeigt Werte alle 10 Sekunden, **6 h** und **24 h** Minutenwerte, **7 T** und **30 T**
Stundenwerte. Die durchgezogene Linie ist der Durchschnitt, die gestrichelte die Spitze im jeweiligen
Abschnitt, die gelbe Linie die Warnschwelle. Mit der Maus über das Diagramm fahren zeigt die Werte.

### System und Prozesse

CPU, Arbeitsspeicher, Laufwerke und die Laufzeit seit dem letzten Neustart. Die Prozessliste zeigt die
für den AI-Betrieb wichtigen Programme (Ollama, Runner, Docker, Python) mit CPU, Speicher und gekürzter
Befehlszeile – Benutzerpfade und Passwörter werden dabei ausgeblendet.

### Export für AI-Chats

**Export** oben rechts in der Statuszeile öffnet einen Dialog, der den aktuellen Stand als Text
zusammenstellt – zum Einfügen in einen Chat (OpenCode, OpenWebUI, Claude) oder zum Ablegen.

| Option | Wirkung |
|---|---|
| **Inhalt** | Zustand (Warnungen, Modelle, GPU, Dienste, Rechner), Ereignisse, Verlauf – einzeln abwählbar |
| **Format** | Markdown mit YAML-Kopf (für Menschen und Modelle) oder JSON (für Programme) |
| **Umfang** | Kurz: Überblick, 10 Ereignisse, Verlauf als Kennzahlen (Ø, Spitze, Min, Zeit über der Warnschwelle). Ausführlich: dazu Prozesse, Laufwerke, 50 Ereignisse, Verlauf als Tabelle (Markdown, höchstens 60 Zeilen) bzw. komplette Zeitreihe (JSON) |
| **Zeitraum Verlauf** | wie im Verlauf-Diagramm: 1 h bis 30 T |
| **Anonymisieren** | ersetzt Rechnername und IPs durch `<ai-host>` / `<ip>`, Benutzerordner durch `<user>` und lässt Befehlszeilen weg. Standard: an – der Text kann in einem Cloud-Chat landen |

Die Vorschau zeigt den fertigen Text mit geschätzter Token-Zahl (Zeichen / 4). **Kopieren** legt ihn in
die Zwischenablage, **Speichern** lädt ihn als Datei herunter (`acc-export-<Datum>-<Uhrzeit>.md`). Die
gewählten Optionen bleiben erhalten, bis die Seite verlassen wird.

Anonymisieren erwischt, was das Control Center kennt (Rechnername, `ai_host`, die Adressen der Dienste,
IPv4-Adressen, Benutzerordner). Modellnamen und GPU-Bezeichnung bleiben stehen. Vor dem Teilen trotzdem
einmal drüberlesen.

### Datenquellen mit Fehlern

Kann eine Quelle (Ollama, GPU, Rechner, Verlauf) nicht gelesen werden, steht sie ganz unten mit
Fehlermeldung. Die übrigen Werte laufen weiter.

## Betrieb

Woher die Werte kommen, steht in `[adapters]` (gilt für alle Module). Was das Dashboard daraus macht,
steht in `[modules.dashboard]`. Alle Werte sind optional; ohne Eintrag gilt der Standard.

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `interval_fast_s` | `2` | Takt für Modelle und GPU (Sekunden) |
| `interval_medium_s` | `10` | Takt für CPU, RAM, Prozesse und Dienste |
| `interval_slow_s` | `60` | Takt für Ollama-Version und Laufwerke |
| `vram_headroom_warn_mib` | `1500` | Warnung, wenn weniger VRAM frei ist |
| `temp_warn_c` | `83` | Warnschwelle GPU-Temperatur |
| `ram_warn_percent` | `90` | Warnschwelle Arbeitsspeicher |
| `disk_warn_free_gib` | `20` | Warnschwelle freier Platz |
| `process_names` | `ollama*`, `llama-server*`, `com.docker.backend`, `python*` | welche Prozesse gelistet werden |
| `process_cmdline` | `short` | Befehlszeilen: `full`, `short` (gekürzt) oder `off` |
| `disk_paths` | Ollama-Modellordner | Laufwerke, die beobachtet werden |
| `retention_raw_h` / `retention_minute_d` / `retention_hour_d` | `24` / `30` / `365` | wie lange der Verlauf aufgehoben wird |
| `retention_events_d` | `90` | wie lange Ereignisse aufgehoben werden |
| `restart_window_s` | `30` | kürzere Ausfälle zählen als „neu gestartet“ |
| `event_quiet_models` | `nomic-embed-text` | Modelle, die nur gezählt werden |
| `ollama_log_paths` | `%LOCALAPPDATA%/Ollama/server*.log` | Log für die Absturz-Erkennung |
| `probes` | MCP, OpenWebUI, Qdrant | Dienste, die geprüft werden (`{ai_host}` wird ersetzt) |

Beispiel:

```toml
[modules.dashboard]
temp_warn_c = 80
event_quiet_models = ["nomic-embed-text", "bge-m3"]
```

**Absturz-Erkennung zeigt „aus“:** Ollama läuft auf einem anderen Rechner (`ai_host` ist nicht lokal),
oder das Log liegt woanders – etwa weil Ollama unter einem anderen Windows-Benutzer läuft. Dann
`ollama_log_paths` mit festem Pfad setzen.

**Export ohne Browser:** dieselben Texte gibt es direkt als Datei – für Skripte, OpenCode oder einen
Browser-Tab. Parameter wie im Dialog: `format` (`md`, `json`), `detail` (`short`, `full`), `parts`
(mehrfach: `snapshot`, `events`, `history`), `range` (`1h` … `30d`), `anonymize` (`true`, `false`).

```powershell
curl.exe -s "http://<AI-Rechner>:8090/api/v1/dashboard/export/raw?format=md&detail=full"
curl.exe -s "http://<AI-Rechner>:8090/api/v1/dashboard/export/raw?format=json&parts=history&range=24h"
```

**Simulieren statt messen:** In `[adapters]` `ollama`, `gpu` und `host` auf `"fake"` setzen und mit
`fake_scenario` ein Szenario wählen (`normal`, `offload`, `idle`, `ollama-down` oder die echten
Aufnahmen `real-normal`, `real-idle`, `real-ollama-down`). Neue Aufnahmen am AI-Rechner:

```powershell
cd backend
uv run python -m control_center.capture_samples real-mein-szenario --note "was zu sehen ist"
```
