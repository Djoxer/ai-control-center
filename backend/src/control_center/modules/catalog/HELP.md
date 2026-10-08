# Katalog

Zeigt die Modelle, die in Ollama installiert sind: woraus sie gemacht wurden, mit welchem Kontext sie
laufen und ob sie komplett auf die GPU passen. Der alte Katalog (`katalog_server.py`, Port 8766) war eine
Marktübersicht aus BenchLM-Daten. Dieser hier fängt bei dem an, was auf dem AI-Rechner wirklich liegt.
Eigene Testläufe und die BenchLM-Daten kommen in den nächsten Schritten dazu.

## Bedienung

### Gruppen nach Ursprung

Pro Ursprungsmodell gibt es eine Karte. Darunter hängen die Modelle, die daraus gemacht wurden, eingerückt
wie ein Stammbaum. Ein Beispiel:

```
qwen3.5:9b                       aus der Bibliothek geladen
└ qwen3.5-9b-64k                 ändert: num_ctx 65536
  └ qwen3.5-9b-64k-code          ändert: temperature 0.2 · System-Prompt (170 Zeichen)
└ qwen35-24k                     gleiche Gewichte wie qwen3.5:9b
```

Woher der Katalog das weiß:

- **Erstellt aus …**: `ollama create` merkt sich das Ausgangsmodell (`parent_model`). Ist es noch
  installiert, ist die Verbindung sicher.
- **Gleiche Gewichte wie …**: Fehlt dieser Vermerk (Modell direkt aus der GGUF-Datei erstellt, oder das
  Ausgangsmodell wurde gelöscht), vergleicht der Katalog die Gewichtsdatei (sha256 aus dem Modelfile).
  Das Modell hängt dann unter dem ältesten Modell mit denselben Gewichten.
- **Ursprung nicht installiert**: Das Ausgangsmodell ist gelöscht, und kein anderes Modell hat dieselben
  Gewichte. Die Karte trägt dann den Namen des fehlenden Modells.

**ändert:** listet, was ein abgeleitetes Modell anders macht als sein Elternmodell: Parameter, System-Prompt
(nur die Länge, der Text bleibt draußen), Template, andere Gewichte.

### Kontext

Die Zahl ist der Kontext, mit dem Ollama das Modell laden würde. Darunter steht, woher er kommt:

| Angabe | Bedeutung |
|---|---|
| **eigener Wert** | Das Modell setzt `num_ctx` selbst (im Modelfile). |
| **Server-Standard** | Kein eigener Wert. Ollama nimmt `OLLAMA_CONTEXT_LENGTH`. |
| **angenommen** | Weder Modell noch Server nennen einen Wert. Der Katalog rechnet mit 4.096. |
| **…, gekürzt** | Der Wert ist größer als die Länge, auf die das Modell trainiert wurde. Ollama kürzt darauf. |

Ein Client wie OpenWebUI oder Continue kann pro Anfrage einen anderen Kontext verlangen. Dann lädt Ollama das
Modell neu, und der Katalog sieht diesen Kontext als eigene Messung.

### VRAM-Bedarf und Prognose

Die Zahl ist der Bedarf in Ollamas Zählung, also dieselbe Größe, die die Übersicht für ein geladenes Modell
zeigt. Der Balken rechnet noch den Treiber dazu (~1,2 GiB, die Ollama nicht mitzählt, gemessen am 07.10.) und
vergleicht mit der Karte. Die Maus über dem Balken zeigt die Rechnung.

Die Farbe sagt, woher die Zahl kommt:

- **grün, „gemessen“**: Ollama hat das Modell mit genau diesem Kontext auf dieser GPU geladen, und der
  Katalog hat es in `/api/ps` gesehen. Das schlägt jede Schätzung.
- **gelb mit ≈, „geschätzt“**: aus den Modelldaten berechnet, bevor das Modell je geladen wurde.
  Gewichte (Dateigröße) + KV-Cache (Schichten × KV-Köpfe × Kopfgröße × Kontext × KV-Typ) + 0,4 GiB
  Rechenpuffer.

Die Prognose rechts:

| Prognose | Bedeutung |
|---|---|
| **passt** | unter 90 % der Karte |
| **knapp** | 90–100 % der Karte. Ein Browser mit GPU-Beschleunigung kann den Ausschlag geben. |
| **Teil-Offload** | mehr als die Karte. Ollama verteilt das Modell auf GPU und CPU. Auf der RTX 5070 Ti stürzt der Runner dabei ab. **num_ctx verkleinern.** |
| **nur CPU** | Ollama hat das Modell ohne GPU geladen (beobachtet). |
| **unklar** | Modelldaten fehlen oder die GPU ist von hier aus nicht sichtbar. |

Bei manchen Architekturen ist die Formel ungenau: Hybrid-Modelle mit rekurrenten Schichten, Sliding Window mit
unbekanntem Schichtplan, MLA (DeepSeek). Die Details sagen dann „Ungenau“. Nach dem ersten Laden zählt die
Messung.

### Details

⋮ → **Details …** zeigt alles zu einem Modell: Herleitung des Kontexts, die Schätzung als Rechnung, alle
Beobachtungen (Kontext, Größe, Anteil auf der GPU, wie oft geladen) und alle Parameter.

### Neu einlesen

Der Katalog liest die Modellliste jede Minute selbst neu ein, `/api/show` nur für neue oder geänderte
Modelle. **Alle neu einlesen** (oben) und ⋮ → **Neu einlesen** (pro Modell) erzwingen das sofort. Dabei wird
kein Modell geladen, die GPU bleibt unberührt.

### Ollama-Standards

Oben steht, was Ollama für Modelle ohne eigene Werte nimmt: Standard-Kontext, KV-Cache-Typ, Flash Attention,
parallele Anfragen, wie viele Modelle gleichzeitig geladen sein dürfen. Die Werte stammen aus der Zeile
„server config“, die Ollama beim Start in sein `server.log` schreibt. Das klappt nur, wenn Ollama auf
demselben Rechner läuft wie das Control Center.

### Nicht mehr installiert

Gelöschte Modelle bleiben 90 Tage in der Liste, samt ihrer Messwerte. Wird ein Modell neu geladen, ist es
wieder da, mit seinem alten „im Katalog seit“.

## Betrieb

### Woher die Daten kommen

| Quelle | Wie oft | Was |
|---|---|---|
| `GET /api/tags` | jede Minute | installierte Modelle, Digest, Größe |
| `POST /api/show` | nur bei neuen/geänderten Modellen | Parameter, Elternmodell, Gewichtsdatei, GGUF-Metadaten |
| `GET /api/ps` | alle 10 s | geladene Modelle mit Kontext und VRAM-Anteil (Messwerte) |
| `server.log` | bei jedem Einlesen (erstes MB der neuesten Datei) | Ollama-Standards |
| NVML | bei jedem Einlesen | GPU-Name und Größe (Hardware-Profil der Messwerte) |

Messwerte werden pro Modell-Digest × Kontext × GPU gespeichert. Ein neu gezogenes Modell (neuer Digest)
fängt also bei null an. Gespeichert wird alles in `data/control-center.db` (Tabellen `catalog_models` und
`catalog_observations`). Ist Ollama nicht erreichbar, zeigt die Seite den letzten bekannten Stand.

### Einstellungen

In `backend/control-center.toml`, alle optional:

```toml
[modules.catalog]
refresh_interval_s = 60          # /api/tags
observe_interval_s = 10          # /api/ps
server_context_length = 65536    # überschreibt OLLAMA_CONTEXT_LENGTH aus server.log
kv_cache_type = "q8_0"           # f16 | q8_0 | q4_0
flash_attention = true
num_parallel = 1
ollama_log_paths = ["%LOCALAPPDATA%/Ollama/server*.log"]
fallback_context_length = 4096   # wenn niemand etwas sagt
clamp_to_trained = true          # Ollama kürzt num_ctx auf die Trainingslänge
graph_reserve_gib = 0.4          # Schätzung: Rechenpuffer
driver_overhead_gib = 1.2        # Schätzung: Treiber über Ollamas Zählung
tight_ratio = 0.9                # ab hier „knapp“
keep_removed_days = 90
```

Auf dem **Zweitrechner** kann das Control Center das Log des AI-Rechners nicht lesen und dessen GPU nicht
sehen. Die Ollama-Standards trägt man dort von Hand ein (Werte wie oben), dann rechnet die Schätzung wie am
AI-Rechner. Mit `[adapters] gpu = "fake"` nimmt der Katalog die GPU aus dem Fake-Szenario (aufgenommen am
AI-Rechner) und schreibt „GPU simuliert“ dazu. Ohne GPU bleibt die Prognose „unklar“.

### Fehlerbilder

| Anzeige | Ursache |
|---|---|
| „Keine Verbindung: …“ | Ollama läuft nicht oder `[adapters] ollama_url` stimmt nicht. Die Liste ist der letzte Stand. |
| „Nur Grunddaten – /api/show fehlgeschlagen“ | Das Modell wurde beim Einlesen gerade gelöscht, oder Ollama hing. Wird beim nächsten Einlesen wiederholt. |
| Ollama-Standards „unbekannt“ | Ollama läuft auf einem anderen Rechner, oder `server.log` hat (noch) keine „server config“-Zeile. Werte in `[modules.catalog]` eintragen. |
| „fake: ollama-tags.json missing“ | Simulation mit einem Szenario, das vor dem Katalog aufgenommen wurde. Szenario neu aufnehmen (`capture_samples`). |
