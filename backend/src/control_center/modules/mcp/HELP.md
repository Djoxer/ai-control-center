# MCP-Server

Startet, stoppt und überwacht die MCP-Server des AI-Rechners. Bisher lief zum Beispiel `mcp_server.py`
in einem eigenen Fenster, das jemand von Hand geöffnet hat. Jetzt übernimmt das Control Center diese
Rolle: Es startet den Server mit, bemerkt Abstürze, startet ihn neu und zeigt, welche Tools er anbietet.

## Bedienung

### Karten und Zustände

Pro eingetragenem Server gibt es eine Karte. Oben stehen Name und Zustand:

| Zustand | Bedeutung |
|---|---|
| **Gestoppt** | läuft nicht |
| **Startet …** | der Prozess läuft, sein Port ist noch nicht offen |
| **Läuft** | der Prozess läuft und sein Port ist offen |
| **Läuft – Port zu** | der Prozess läuft, nimmt aber keine Verbindungen an (hängt er? falscher Port in der `url`?) |
| **Stoppt …** | der Prozess wird gerade beendet |
| **Abgestürzt** / **Beendet** | er hat sich ohne Auftrag beendet. Exit-Code und die letzten Ausgabezeilen stehen in der Karte |

Darunter folgen Laufzeit, PID, Port, Neustarts, die Adresse (zum Kopieren) und der Befehl, mit dem der
Server gestartet wird, samt Arbeitsordner.

### Aktionen

- **Starten** startet sofort, ohne Rückfrage.
- **Stoppen** und **Neu starten** fragen vorher nach, weil alle Clients (Continue, OpenCode,
  OpenWebUI) die Verbindung verlieren. Beendet wird der ganze Prozessbaum. Unter Windows ist das
  wichtig, weil das `python.exe` einer venv nur ein Starter ist. Der eigentliche Server läuft als
  dessen Kindprozess und würde sonst weiterlaufen und den Port blockieren.
- **Zurücksetzen** gibt es nur nach einem Absturz. Damit wird der Absturz quittiert und ein geplanter
  automatischer Neustart abgebrochen.

Eine Anmeldung gibt es noch nicht, sie kommt mit dem Login über OpenWebUI. Bis dahin werden nur
Anfragen abgewiesen, die eine fremde Webseite im Browser auslöst.

### Hinweise in der Karte

| Hinweis | Was tun |
|---|---|
| **Port belegt** | Ein anderer Prozess hört auf dem Port, meist der Server im alten Fenster. Dort mit Strg+C beenden, dann hier **Starten**. Fremde Prozesse beendet das Control Center nie selbst. |
| Programm nicht gefunden / Arbeitsordner fehlt | Pfad in `command` bzw. `cwd` prüfen (siehe Betrieb). |
| Port nach 30 s noch nicht offen | Der Server läuft, antwortet aber nicht. Die Ausgabe im Protokoll zeigt meist den Grund. |
| Abgestürzt … Neustart in 2 s | Der automatische Neustart läuft (siehe unten). |

### Automatischer Neustart

Nach einem Absturz startet das Control Center den Server nach 2, 4 und 8 Sekunden neu, höchstens
dreimal innerhalb von 5 Minuten. Danach gibt es auf, damit ein dauerhaft kaputter Server nicht ewig
neu startet. Ein Start von Hand setzt den Zähler zurück. Pro Server abschaltbar mit
`restart_on_crash = false`.

### Tools

**Abfragen** fragt den Server über das MCP-Protokoll (`tools/list`), welche Tools er anbietet: Name,
Beschreibung und Parameter. Ein `*` markiert Pflichtangaben, `= 8` den Standardwert. Nach jedem Start
geschieht das automatisch. Das klappt auch bei einem Server, der woanders gestartet wurde, solange
seine `url` antwortet.

### Ausgabe

**Ausgabe im Protokoll** öffnet die Protokoll-Seite mit der Quelle **MCP: <Name>**. Dort steht alles,
was der Server ausgibt (Logger `output`), dazu Start, Stopp und Absturz (Logger `supervisor`). Die Datei
liegt in `data/logs/mcp-<key>.log` und rotiert bei 5 MB.

## Betrieb

### Server eintragen

In `backend/control-center.toml`, ein Block pro Server:

```toml
[[modules.mcp.servers]]
key = "bent-rag"                       # kleingeschrieben, wird Teil von Adresse und Logdatei
title = "Bent/TYPO3-RAG"
command = ["C:/<RAG-ORDNER>/.venv/Scripts/python.exe", "mcp_server.py"]
cwd = "C:/<RAG-ORDNER>"
url = "http://127.0.0.1:8000/mcp"
autostart = true
```

| Feld | Bedeutung |
|---|---|
| `command` | Programm und Argumente als Liste. Gestartet wird **ohne Shell**: kein `cd`, kein `&&`, keine venv-Aktivierung. Stattdessen direkt das `python.exe` der venv eintragen. `{python}` steht für das Python des Control Centers; Umgebungsvariablen wie `%USERPROFILE%` werden ersetzt. |
| `cwd` | Arbeitsordner. Relativ heißt: neben `control-center.toml`. Ohne Angabe gilt der Ordner dieser Datei (normalerweise `backend`). |
| `url` | MCP-Adresse, hier immer mit `127.0.0.1`, denn Control Center und Server laufen auf demselben Rechner. Clients im LAN nutzen weiter die IP des AI-Rechners. Aus der `url` kommen der Port (Bereitschaft, Belegung) und die Tool-Abfrage. |
| `autostart` | `true` startet den Server mit dem Control Center. Standard: `false` |
| `restart_on_crash` | automatischer Neustart nach Absturz. Standard: `true` |
| `env` | zusätzliche Umgebungsvariablen, z. B. `env = { QDRANT_URL = "http://localhost:6333" }` |

Windows-Pfade in TOML entweder mit `/` schreiben oder in einfache Anführungszeichen setzen
(`'C:\rag\.venv\Scripts\python.exe'`), denn in `"…"` ist `\` ein Escape-Zeichen. Änderungen wirken
nach einem Neustart des Control Centers. Gestartet werden kann nur, was in dieser Datei steht: Die
Oberfläche schickt nie einen Befehl, sondern nur den `key`.

### Umstieg vom eigenen Fenster

1. Den Server im alten Fenster mit Strg+C beenden.
2. Den Block oben mit den echten Pfaden eintragen und das Control Center neu starten.
3. Die Karte zeigt **Läuft** und die Tools (`search_bent_php`, …). Der Port bleibt 8000, deshalb
   müssen die Clients nichts ändern.

Wer Schritt 1 vergisst, sieht **Port belegt** mit der PID des alten Fensters, und es passiert nichts
Schlimmes.

### Beenden und Abstürze des Control Centers

- **Strg+C im Fenster des Control Centers:** Es beendet alle Server, die es gestartet hat.
- **Fenster geschlossen:** Windows beendet die Server normalerweise mit, denn sie hängen am selben
  Konsolenfenster. Falls doch einer übrig bleibt, räumt ihn der nächste Start auf (nächster Punkt).
- **Control Center abgestürzt oder per Task-Manager beendet:** Die Server laufen weiter. Beim nächsten
  Start erkennt das Control Center sie an PID und Startzeit (`data/mcp/<key>.json`), beendet sie und
  startet sie mit `autostart` neu. Ein Prozess, dessen PID zufällig wiederverwendet wurde, wird nicht
  angefasst.

### Weitere Einstellungen

```toml
[modules.mcp]
start_timeout_s = 30       # so lange darf das Öffnen des Ports dauern ("uv run" installiert evtl. erst)
stop_timeout_s = 5         # höflich beenden, danach hart
max_restarts = 3           # automatische Neustarts ...
restart_window_s = 300     # ... innerhalb dieser Sekunden, dann aufgeben
restart_backoff_s = 2      # Wartezeit vor Neustart n: 2, 4, 8 s
check_interval_s = 10      # wie oft geprüft wird, wer auf den Ports hört
tools_timeout_s = 10       # Zeitlimit der Tool-Abfrage
log_max_bytes = 5242880    # Logdatei pro Server, dann Rotation
log_backup_count = 3
```

### Demo-Server zum Ausprobieren

Für den Entwicklungsrechner, auf dem kein echter MCP-Server läuft:

```toml
[[modules.mcp.servers]]
key = "demo"
title = "Demo-Server"
command = ["{python}", "-m", "control_center.modules.mcp.demo_server", "--port", "8701"]
url = "http://127.0.0.1:8701/mcp"
```

Der Demo-Server kennt drei Tools (`echo`, `add`, `server_time`) und hört nur auf diesem Rechner.

### Wenn etwas nicht stimmt

| Symptom | Ursache und Abhilfe |
|---|---|
| Seite leer mit Beispiel | Kein Server in `[[modules.mcp.servers]]` eingetragen, oder das Control Center wurde danach nicht neu gestartet |
| Modul **failed** (⋮ → Über) | Fehler im Abschnitt `[modules.mcp]`, z. B. doppelter `key` oder zwei Server auf demselben Port. Der Text nennt das Feld |
| Tools: „Abfrage fehlgeschlagen“ | Server läuft nicht, `url` falsch (Pfad `/mcp`?) oder er spricht kein Streamable HTTP |
| Abgestürzt mit `ModuleNotFoundError` in der letzten Ausgabe | falsches Python: das `python.exe` der venv des Servers eintragen, nicht das System-Python |
| Umlaute kaputt in der Ausgabe | Das Control Center setzt für Python-Server `PYTHONIOENCODING=utf-8`; andere Programme geben vielleicht cp1252 aus |
