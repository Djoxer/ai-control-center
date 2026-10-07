# Allgemein

Das AI Control Center ist die **Kontrollebene** für den AI-Rechner: Was ist geladen, wie voll ist die
Grafikkarte, laufen alle Dienste, was steht in den Logs. Gearbeitet wird woanders – Chats, Notizen und
Wissenssammlungen bleiben in OpenWebUI. Hier wird überwacht und gesteuert.

## Bedienung

### Aufbau

- **Seitenleiste links:** oben die Arbeitsseiten (Übersicht, Katalog), unten die Werkzeuge (Protokoll,
  Einstellungen). Ein Eintrag mit grauer Plakette wie `unknown` oder `failed` gehört zu einem Modul, das
  im Backend gerade nicht läuft – die Seite öffnet sich trotzdem, zeigt aber keine echten Daten.
  Auf dem Handy klappt die Leiste über das Menüsymbol oben links auf.
- **Kopfzeile:** links der Name der Seite, rechts der Status-Chip und das Menü **⋮**.
- **Menü ⋮:** diese Hilfe, „Über AI Control Center“ (Version, Zustand der Module) und die
  API-Dokumentation (`/docs`, öffnet in einem neuen Tab).

### Status-Chip

| Anzeige | Bedeutung |
|---|---|
| **ok** (grün) | Backend erreichbar, Datenbank in Ordnung, alle Module laufen |
| **degraded** (gelb) | Backend läuft, aber ein Modul ist ausgefallen oder die Datenbank streikt – Details unter ⋮ → Über |
| **reconnecting** (blau, pulsiert) | Die Live-Verbindung ist kurz weg und baut sich selbst wieder auf |
| **offline** (rot) | Backend nicht erreichbar – meist läuft der Prozess am AI-Rechner nicht |

Ein Klick auf den Chip fragt den Zustand sofort neu ab.

### Simulierte Daten

Steht auf einer Seite **„Simulierte Daten: …“**, kommen die genannten Werte nicht vom echten Rechner,
sondern aus einem aufgezeichneten Szenario. Das ist zum Entwickeln und für Vorführungen gedacht und
wird in der Konfiguration eingeschaltet (siehe Betrieb).

## Betrieb

### Starten

Das Control Center ist **ein** Prozess: Das Python-Backend liefert die Daten und gleichzeitig die
Oberfläche aus, Port 8090.

```powershell
cd C:\_working_dir\projects\ai-control-center\backend
uv run python -m control_center
```

Danach erreichbar unter `http://<AI-Rechner>:8090/`. Das Backend läuft, solange das Fenster offen ist –
einen Autostart gibt es noch nicht.

### Konfiguration

Alles steht in `backend/control-center.toml` (Vorlage: `control-center.example.toml`). Jeder Wert lässt
sich auch per Umgebungsvariable setzen, z. B. `ACC_PORT=9000` oder `ACC_LOG__LEVEL=DEBUG`.
Änderungen wirken nach einem Neustart.

| Abschnitt | Wofür |
|---|---|
| oben (ohne Abschnitt) | `host`, `port`, `data_dir`, `frontend_dist` (gebaute Oberfläche) |
| `[log]` | Level und Rotation der eigenen Logdatei |
| `[adapters]` | Wo Ollama, GPU und Rechner abgefragt werden – echt oder simuliert (`fake`) |
| `[modules]` | `disabled = ["…"]` schaltet ein Modul ab, ohne Code anzufassen |
| `[modules.<name>]` | Einstellungen eines Moduls, siehe die Hilfe des jeweiligen Moduls |

Wichtig: Werte ohne Abschnitt wie `frontend_dist` müssen **vor** der ersten `[…]`-Zeile stehen.

### Aktualisieren

```powershell
cd C:\_working_dir\projects\ai-control-center
git pull
cd backend
uv sync
cd ..\frontend
npm ci
npm run build
```

Danach das Backend neu starten. `uv sync` und `npm ci` sind nur nötig, wenn sich Abhängigkeiten oder die
Version geändert haben – schaden aber nie. Am AI-Rechner wird nie committet; Änderungen kommen vom
Entwicklungsrechner per `git pull`.

### Daten

Im Ordner `data/` (neben `backend/`): die SQLite-Datenbank `control-center.db` (Verlauf, Ereignisse)
und `logs/control-center.log` (eigenes Log, JSON-Zeilen, rotiert). Löschen setzt Verlauf und Ereignisse
zurück, sonst nichts.

### Wenn etwas nicht stimmt

| Symptom | Ursache und Abhilfe |
|---|---|
| Seite bleibt weiß | Alte Seite im Browser-Cache: einmal **Strg+F5** |
| „frontend build missing“ | Oberfläche nicht gebaut: `npm run build` im Ordner `frontend` |
| Status **offline** | Backend-Fenster am AI-Rechner geschlossen – neu starten |
| Modul **failed** | Ursache unter ⋮ → Über und im Protokoll (Quelle „AI Control Center“) |
| Version im Über-Dialog veraltet | Nach `git pull` fehlte `uv sync` |
