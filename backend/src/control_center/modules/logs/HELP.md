# Protokoll (Logs)

Liest die Logdateien des Control Centers und von Ollama, filtert sie und zeigt neue Zeilen live –
ohne dass jemand am AI-Rechner Dateien öffnen muss.

## Bedienung

### Quellen

Oben ein Knopf pro Quelle, z. B. **AI Control Center**, **Ollama** und je MCP-Server **MCP: …**.
Rechts stehen Anzahl und Größe der Dateien und welche gerade beschrieben wird. Eine Quelle mit
„(keine Datei)“ ist eingerichtet, aber ihr Pfad passt auf keine Datei. Von anderen Seiten aus öffnet
ein Link wie „Ausgabe im Protokoll“ direkt die passende Quelle.

### Filter

| Filter | Wirkung |
|---|---|
| **Level ab** | nur Einträge ab dieser Stufe; Zeilen ohne Stufe fallen dann weg |
| **Logger beginnt mit** | z. B. `control_center.modules.dashboard` für alles aus dem Dashboard |
| **Suche** | Text in Meldung oder Traceback, Groß-/Kleinschreibung egal |
| **API-Zugriffe ausblenden** | blendet die Zeile aus, die jeder Seitenaufruf schreibt – sonst sieht man vor lauter Zugriffen nichts |
| **Live** | neue Zeilen erscheinen sofort oben; der grüne Punkt zeigt die stehende Verbindung |

Filter wirken auch auf die Live-Zeilen.

### Einträge

Die neuesten stehen oben. Hat ein Eintrag einen Traceback, steht dort **[Traceback anzeigen]** – ein
Klick auf die Zeile klappt ihn auf und wieder zu. **Ältere laden** blättert zurück, auch über
rotierte Dateien hinweg; „Anfang erreicht“ heißt, es gibt nichts Älteres mehr.

## Betrieb

Die eigene Logdatei `data/logs/control-center.log` ist immer dabei (JSON-Zeilen, Rotation laut
`[log]`: standardmäßig bei 10 MB, 5 alte Dateien). Weitere Quellen stehen in `[modules.logs]`:

```toml
[modules.logs]
tail_interval_s = 1.0          # wie oft nach neuen Zeilen gesehen wird (Sekunden)
max_page_size = 1000           # Obergrenze pro Seite

[[modules.logs.sources]]
key = "ollama"                 # kleingeschrieben, wird Teil der Adresse
title = "Ollama"
format = "text"                # "json" für JSON-Zeilen, sonst "text"
paths = ["%LOCALAPPDATA%/Ollama/server*.log"]
```

- **`paths`** sind Muster: `*` passt auch auf rotierte Dateien (`server-1.log`). Umgebungsvariablen
  wie `%LOCALAPPDATA%` werden ersetzt. Die Pfade gelten auf dem Rechner, auf dem das Backend läuft.
- Wer `sources` setzt, ersetzt die Standardliste – Ollama dann wieder mit aufführen.
- Andere Module bringen eigene Quellen mit, ohne Eintrag hier: jeder MCP-Server erscheint als
  **MCP: <Name>** (siehe Hilfe „MCP-Server“).
- Zeilen im Format von Ollama (`time=… level=… msg=…`) werden zerlegt; alles andere erscheint als
  Rohtext ohne Stufe.
- Die Dateien werden nur zum Lesen kurz geöffnet. Rotation und Löschen durch andere Programme
  funktionieren weiter.
