# RAG

Hält die Code-Sammlungen in Qdrant aktuell, die der MCP-Server durchsucht (`search_bent_php`,
`search_bent_angular`, `search_typo3`). Bisher erledigten das zwei Skripte, `index_bent.py` und
`index_typo3.py`, die jemand von Hand gestartet hat. Jetzt stehen die Quellen in der Konfiguration, und
die Indexierung läuft per Knopf im Hintergrund, mit Fortschritt, Bericht und Secret-Filter.

Die Suchqualität ist dabei bewusst **dieselbe wie bei den Skripten**: eine Datei ergibt einen Eintrag
(Punkt), und gespeichert werden die ersten 6000 Zeichen. Das ist der Ausgangsstand, gegen den spätere
Verbesserungen gemessen werden (siehe „Was noch nicht drin ist“).

## Bedienung

### Die Seite im Überblick

- **Statuszeile:** Wo Qdrant läuft (Adresse, Version), welches Modell einbettet, und ob von hier aus
  geschrieben werden darf.
- **Quellen:** Eine Karte pro eingetragener Quelle mit Ordner, Regeln, Größe der Collection und dem
  letzten Lauf.
- **Indexierung:** Fortschritt des laufenden Jobs bzw. der Bericht des letzten.
- **Collections:** Alles, was in Qdrant liegt, auch Collections, die keine Quelle hier verwaltet
  (z. B. eine alte `bent`-Collection).
- **Testsuche:** Eine Frage stellen und sehen, welche Dateien das MCP-Tool liefern würde.

Für Details zu einzelnen Punkten hat Qdrant ein eigenes Web-Interface unter
`http://<AI-Rechner>:6333/dashboard`. Der Link steht in der Statuszeile.

### Neu indexieren

**Neu indexieren** auf einer Quellen-Karte erneuert eine Collection, **Alle neu indexieren** arbeitet
alle Quellen nacheinander ab. Es läuft immer nur eine Indexierung gleichzeitig. Pro Quelle passiert
Folgendes:

1. **Dateien suchen:** Die Include-Ordner werden durchlaufen, mit denselben Regeln wie in den Skripten
   (Endungen, ausgeschlossene Namen und Ordner).
2. **Prüfen und einbetten:** Jede Datei durchläuft den Secret-Filter. Danach wird sie auf 6000 Zeichen
   gekürzt und von Ollama in einen Vektor umgerechnet (`nomic-embed-text`).
3. **Schreiben:** Die Punkte gehen in Paketen zu 50 nach Qdrant. Fehlt die Collection, wird sie
   angelegt.
4. **Aufräumen:** Punkte von Dateien, die es nicht mehr gibt oder die jetzt der Secret-Filter
   aussortiert, werden gelöscht.

Jede Datei behält bei jedem Lauf dieselbe ID (berechnet aus Collection und Dateipfad). Ein zweiter Lauf
überschreibt also die alten Punkte, statt neue danebenzulegen.

**Gut zu wissen:**

- Auf dem AI-Rechner ist `OLLAMA_MAX_LOADED_MODELS=1` gesetzt. Sobald die Indexierung startet, entlädt
  Ollama deshalb das gerade geladene Chat-Modell. Wer während des Laufs chattet, wartet danach auf das
  Neuladen. Am besten indexiert man, wenn gerade niemand mit dem lokalen Modell arbeitet.
- Der MCP-Server sucht während des Laufs ganz normal weiter. Jede Datei hat in jedem Moment entweder
  ihren alten oder ihren neuen Punkt.
- **Abbrechen** stoppt nach der aktuellen Datei. Was bis dahin eingebettet wurde, bleibt erhalten,
  gelöscht wird nichts. Die Collection ist dann eine Mischung aus alt und neu, der nächste vollständige
  Lauf bringt alles auf einen Stand.

### Der Bericht

| Zahl | Bedeutung |
|---|---|
| **Dateien** | passende Dateien in den Include-Ordnern |
| **Indexiert** | als Punkt in Qdrant geschrieben |
| **Abgeschnitten** | länger als 6000 Zeichen. Gespeichert und durchsuchbar ist nur der Anfang, der Rest der Datei ist für die Suche unsichtbar. Die Liste nennt jede betroffene Datei |
| **Leer** | Datei ohne Inhalt (oder nur Leerzeichen), wird übersprungen wie in den Skripten |
| **Secret-Filter** | aussortiert, weil sie nach Zugangsdaten aussieht (siehe unten) |
| **Übersprungen** | nicht lesbar, oder Ollama hat einen Fehler gemeldet. Der alte Punkt der Datei bleibt stehen |
| **Entfernt** | beim Aufräumen gelöscht: Punkte gelöschter Dateien, gefilterter Dateien und alte Punkte aus den Skripten |

Fehlt ein Include-Ordner, steht das als Hinweis im Bericht. Der Bericht der letzten Läufe überlebt
einen Neustart (`data/rag/jobs.json`), die Einzelheiten stehen auch im Protokoll unter
**RAG-Indexierung**.

### Secret-Filter

Alles, was in Qdrant liegt, kann jeder im LAN über die MCP-Tools wieder abrufen. Die Skripte haben nur
`.env` und `.htpasswd` ausgelassen. Ein Datenbank-Passwort in einem PHP-Konfig-Array oder ein Token in
einer YAML-Datei landete im Index. Der Filter prüft jetzt jede Datei vor dem Einbetten:

- **Dateinamen** wie `.env.local`, `secrets.yaml`, `id_rsa`, `*.pem`
- **Inhalte** wie `'password' => '…'`, `apiKey: "…"`, `mysql://benutzer:passwort@host`, private Schlüssel,
  bekannte Token-Formate (GitHub, AWS, Slack, JWT …)

Platzhalter wie `'changeme'`, `'${DB_PASS}'` oder Validierungsregeln (`'required|min:8'`) zählen nicht.
Bei einem Treffer wird die Datei **nicht** indexiert, und ein vorhandener alter Punkt wird beim
Aufräumen gelöscht. Der Bericht nennt Datei, Zeile und Regel, aber nie den gefundenen Wert.

Ist ein Treffer harmlos (z. B. ein Beispielwert in einer Demo-Datei), gibt `secret_allow` die Datei
frei (siehe Betrieb). Sie wird dann indexiert und im Bericht als „freigegeben“ geführt.

### Testsuche

Collection wählen, Frage eingeben, **Suchen**. Die Frage wird genauso eingebettet wie im MCP-Tool,
deshalb zeigt die Liste genau die Dateien, die ein Client wie OpenCode bekommen würde, in derselben
Reihenfolge. Der **Score** ist die Kosinus-Ähnlichkeit: 1 bedeutet identisch, bei Code liegen gute
Treffer oft nur bei 0,5 bis 0,7. **Als MCP-Antwort kopieren** liefert den Text im Format des
MCP-Servers.

Auch die Testsuche lädt `nomic-embed-text` in Ollama und verdrängt damit das Chat-Modell.

### Collection löschen

In der Tabelle **Collections** über ⋮ → **Löschen**. Zur Bestätigung muss der Name der Collection
eingetippt werden, denn Löschen lässt sich nicht rückgängig machen. Ist es eine verwaltete Collection,
liefert das passende MCP-Tool bis zur nächsten Indexierung keine Treffer mehr.

## Betrieb

### Quellen eintragen

In `backend/control-center.toml`, ein Block pro Collection. So sehen die beiden Bent-Collections der
alten Skripte aus:

```toml
[modules.rag]
qdrant_url = "http://{ai_host}:6333"

[[modules.rag.sources]]
collection = "bent_php"
title = "Bent PHP-Backend"
path = "%USERPROFILE%/rag-setup/repos/bent/bent-php-api"
includes = [
  { dir = "src", ext = [".php"] },
  { dir = "app", ext = [".php"] },
  { dir = "db/migrations", ext = [".sql"] },
]

[[modules.rag.sources]]
collection = "bent_angular"
title = "Bent Angular-Frontend"
path = "%USERPROFILE%/rag-setup/repos/bent/bent-angular-bf"
includes = [{ dir = "src/app", ext = [".ts"] }]
```

| Feld | Bedeutung |
|---|---|
| `collection` | Name in Qdrant. Muss zu den MCP-Tools passen (`bent_php`, `bent_angular`, `typo3`). Eine Quelle pro Collection: Die Quelle „besitzt“ ihre Collection, beim Aufräumen wird alles gelöscht, was nicht zu ihren Dateien gehört |
| `path` | Ordner des Repositorys. `%USERPROFILE%` und andere Umgebungsvariablen werden ersetzt, relativ heißt: neben `control-center.toml`. Der Code bleibt außerhalb des Control-Center-Repos, Kundencode gehört nicht nach GitHub |
| `includes` | Unterordner und Endungen. Groß-/Kleinschreibung zählt (`.php` nimmt kein `.PHP`), und `.ts` nimmt auch `x.spec.ts`, genau wie in den Skripten |
| `exclude_names` | Dateinamen, die nie gelesen werden. Standard: `[".env", ".htpasswd"]` |
| `exclude_dirs` | Ordnernamen, in die nicht abgestiegen wird. Standard: `["vendor", "node_modules", ".git", "var"]`. Das alte TYPO3-Skript hatte keine, dort `exclude_dirs = []` setzen, damit es genau gleich bleibt |
| `secret_allow` | Dateien, die trotz Secret-Treffer indexiert werden, als Muster auf den relativen Pfad, z. B. `["config/demo.php"]` |

Windows-Pfade entweder mit `/` schreiben oder in einfache Anführungszeichen setzen, denn in `"…"` ist `\`
ein Escape-Zeichen. Änderungen wirken nach einem Neustart des Control Centers.

### Weitere Einstellungen

```toml
[modules.rag]
store = "qdrant"                 # qdrant | memory (Entwicklungsrechner, siehe unten)
qdrant_url = "http://{ai_host}:6333"
qdrant_timeout_s = 30
allow_remote_writes = false      # siehe Schreibschutz
embedder = "ollama"              # ollama | fake (Entwicklungsrechner)
# ollama_url = "http://{ai_host}:11434"   # Standard: ollama_url aus [adapters]
embedding_model = "nomic-embed-text"
embed_timeout_s = 120            # der erste Aufruf lädt das Modell
distance = "Cosine"              # nur für neu angelegte Collections
max_chars = 6000                 # wie die Skripte
batch_size = 50                  # Punkte pro Schreibvorgang
max_consecutive_errors = 10      # so viele Einbettungsfehler hintereinander -> Abbruch
search_limit_max = 50
keep_jobs = 20                   # Berichte in data/rag/jobs.json
```

### Schreibschutz

Läuft Qdrant auf einem **anderen** Rechner als das Control Center, sind „Neu indexieren“ und „Löschen“
gesperrt, die Seite sagt das auch. Das betrifft den Entwicklungsrechner: Mit `ai_host` = IP des
AI-Rechners zeigt er die echten Collections und kann testweise darin suchen, überschreibt sie aber
nicht aus Versehen. „Anderer Rechner“ heißt: Die Adresse in `qdrant_url` ist weder `localhost`/`127.0.0.1`
noch eine eigene Netzwerkadresse. Wer es wirklich will, setzt `allow_remote_writes = true`.

Achtung: Auch die Testsuche vom Entwicklungsrechner aus lädt das Einbettungsmodell auf dem AI-Rechner.

### Entwicklungsrechner ohne Qdrant und Ollama

```toml
[modules.rag]
store = "memory"                 # Collections nur im Speicher, weg nach Neustart
embedder = "fake"                # Wort-Hashing statt Modell, schnell und ohne Ollama
fake_delay_s = 0.05              # macht den Fortschritt sichtbar

[[modules.rag.sources]]
collection = "acc_demo"
title = "Control Center (Demo)"
path = "src"                     # der eigene Code, relativ zu backend/
includes = [{ dir = "control_center", ext = [".py", ".md"] }]
```

Die Fake-Vektoren taugen nur zum Durchklicken. Sie haben eine andere Größe (256 statt 768) und sind mit
`nomic-embed-text` nicht vergleichbar.

### Umstieg von den Skripten

1. Die Quellen wie oben eintragen, mit denselben Collection-Namen. Für TYPO3 eine dritte Quelle mit den
   vier Ordnern aus `index_typo3.py` und `exclude_dirs = []`.
2. Control Center neu starten. Die Karten zeigen die vorhandenen Collections mit ihren Punkten.
3. **Alle neu indexieren.** Der erste Lauf schreibt neue Punkte mit festen IDs und entfernt am Ende die
   alten Punkte der Skripte (IDs 0, 1, 2 …). Bis dahin liefert eine Suche manche Dateien doppelt.
4. Die Skripte werden nicht mehr gebraucht. `mcp_server.py` läuft unverändert weiter, denn er liest
   dieselben Felder (`filename`, `text`).

Unterschied im Ergebnis: Dateinamen stehen jetzt als `src/Domain/Note/Note.php` im Treffer, vorher als
`\src\Domain\Note\Note.php`. Eine Datei, die zwei überlappende Include-Ordner erfassen, wird nur noch
einmal gespeichert.

### Qdrant aus dem Repo starten

`deploy/qdrant/docker-compose.yml` startet Qdrant mit **fester Version**, statt mit `latest` (ein
`docker compose pull` könnte sonst unbemerkt eine neue Version holen, und Qdrant verträgt nur Sprünge um
eine Minor-Version). Die Daten liegen außerhalb des Repos, der Ordner kommt aus der Umgebungsvariable
`ACC_QDRANT_DATA`. Vor dem Umstieg die laufende Version vergleichen (Statuszeile dieser Seite), Einzelheiten
stehen in der Datei selbst.

### Wenn etwas nicht stimmt

| Symptom | Ursache und Abhilfe |
|---|---|
| Qdrant **nicht erreichbar** | Docker Desktop läuft nicht, oder der Container ist aus: `docker ps`, sonst `docker compose up -d` im Qdrant-Ordner |
| „Modell nomic-embed-text fehlt in Ollama“ | einmal `ollama pull nomic-embed-text` auf dem AI-Rechner |
| „Vektorgröße passt nicht“ | Die Collection wurde mit einem anderen Modell angelegt (oder mit `embedder = "fake"`). Passendes Modell eintragen oder die Collection löschen und neu indexieren |
| „Keine passenden Dateien gefunden“ | Pfad, Ordner oder Endungen falsch. Zur Sicherheit wird dann nichts aufgeräumt |
| Quelle **fehlgeschlagen**: Ordner fehlt | `path` prüfen. Steht `%USERPROFILE%` drin, muss das Control Center unter dem Benutzer laufen, dem der Ordner gehört |
| Eine harmlose Datei landet im Secret-Filter | Zeile im Bericht prüfen, dann die Datei in `secret_allow` eintragen |
| Neu indexieren ist ausgegraut | Schreibschutz (anderer Rechner), oder es läuft schon ein Job |

### Was noch nicht drin ist

Diese Punkte ändern die Suchergebnisse und kommen deshalb erst mit einer gemessenen Qualitätsrunde
(Testfragen, Trefferquote vorher/nachher):

- Aufteilen langer Dateien in Abschnitte (Chunking) statt Abschneiden nach 6000 Zeichen
- nur geänderte Dateien neu einbetten (Hashes)
- die von `nomic-embed-text` empfohlenen Präfixe `search_document:` / `search_query:`
- Einbetten in Paketen über `/api/embed`
