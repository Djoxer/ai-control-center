# Katalog

Zeigt die Modelle, die in Ollama installiert sind: woraus sie gemacht wurden, mit welchem Kontext sie
laufen, ob sie komplett auf die GPU passen und wie schnell sie im eigenen Testlauf antworten. Der alte Katalog
(`katalog_server.py`, Port 8766) war eine Marktübersicht aus BenchLM-Daten. Dieser hier fängt bei dem an, was
auf dem AI-Rechner wirklich liegt. Die BenchLM-Daten kommen im nächsten Schritt dazu.

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
- **Kopie von …**: Zwei Namen, ein Modell (`ollama cp`, gleicher Digest). Der jüngere Name hängt unter dem
  älteren.
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
| **gewählt** | Nur im Testlauf: der Kontext, den man dort ausgesucht hat. |

Ein Client wie OpenWebUI oder Continue kann pro Anfrage einen anderen Kontext verlangen. Dann lädt Ollama das
Modell neu, und der Katalog sieht diesen Kontext als eigene Messung.

### VRAM-Bedarf und Prognose

Die Zahl ist der Bedarf in Ollamas Zählung, also dieselbe Größe, die die Übersicht für ein geladenes Modell
zeigt. Der Balken vergleicht sie mit dem **GPU-Budget**: dem Teil der Karte, der für Ollama übrig bleibt. Die
Maus über dem Balken zeigt beide Zahlen.

Die Farbe sagt, woher die Zahl kommt:

- **grün, „gemessen“**: Ollama hat das Modell mit genau diesem Kontext auf dieser GPU geladen, und der
  Katalog hat es in `/api/ps` gesehen, oder ein Testlauf hat es gemessen. Das schlägt jede Schätzung.
- **gelb mit ≈, „geschätzt“**: aus den Modelldaten berechnet, bevor das Modell je so geladen wurde.
  Gewichte (Dateigröße) + KV-Cache (Schichten × KV-Köpfe × Kopfgröße × Kontext × KV-Typ) + 0,4 GiB
  Rechenpuffer.
- **gelb mit ≈, „kalibriert“**: wie geschätzt, aber an einer Messung derselben Gewichte ausgerichtet (siehe
  unten). Deutlich genauer als die reine Formel.

Die Prognose rechts:

| Prognose | Bedeutung |
|---|---|
| **passt** | höchstens 90 % des Budgets |
| **knapp** | 90–100 % des Budgets. Ein Browser mit GPU-Beschleunigung kann den Ausschlag geben. |
| **Teil-Offload** | mehr als das Budget. Ollama verteilt das Modell auf GPU und CPU. Auf der RTX 5070 Ti stürzt der Runner dabei ab. **num_ctx verkleinern.** |
| **nur CPU** | Ollama hat das Modell ohne GPU geladen (beobachtet). |
| **unklar** | Modelldaten fehlen oder die GPU ist von hier aus nicht sichtbar. |

### GPU-Budget

Oben unter den Ollama-Standards steht die Rechnung:

```
Karte              15,9 GiB   (NVML)
andere Programme  − 1,4 GiB   gemessen, als Ollama nichts geladen hatte
Reserve Ollama    − 0,45 GiB  hält Ollama selbst frei (Annahme, ~457 MiB)
verfügbar         = 14,1 GiB  dagegen misst der Balken
```

„Andere Programme“ (Desktop, Browser, Videoplayer) misst der Katalog selbst: Immer wenn `/api/ps` leer ist,
gehört alles, was die Karte belegt, jemand anderem. Gezählt wird erst, wenn zwei Blicke hintereinander
(10 s Abstand) dasselbe zeigen. Ein Modell, das Ollama gerade lädt, belegt schon VRAM, steht aber noch nicht
in `/api/ps`, und darf nicht stundenlang als „andere Programme“ gelten. Bis zur ersten Messung gilt
`other_usage_gib` (1,2 GiB) und die Zeile sagt „angenommen“.

### Kalibrierung

Die Formel kennt nicht jede Architektur: Bei `qwen3.5` (mit Bild-Encoder in der Datei) lag sie am AI-Rechner
rund 0,9 GiB zu hoch. Hybrid-Modelle, Sliding Window und MLA (DeepSeek) haben eigene Speicherpläne. Deshalb richtet
der Katalog jede Schätzung an Messungen **derselben Gewichte auf derselben GPU** aus, auch wenn sie von einem
anderen Namen stammen (`qwen3.5-9b-64k` kalibriert `qwen3.5:9b`):

- **eine Messung**: gemessene Größe − Formel-KV beim gemessenen Kontext + Formel-KV beim Zielkontext. Der
  Fehler der Formel bei Gewichten und Puffer fällt so heraus.
- **zwei und mehr**: eine Gerade durch die kleinste und die größte gemessene Kontextlänge. Damit stimmen auch
  die Kosten pro Token.

Gemessen wird pro Kontextlänge der letzte Stand. Wer den KV-Cache-Typ am Server ändert (f16 ↔ q8_0), sollte
danach neu messen (Testlauf): Ältere Messungen gelten noch für den alten Typ.

Die Details zeigen beides: „= nach Formel“ und darunter fett zum Beispiel „= an 1 Messung (65.536 Token)
kalibriert“.

### Testlauf

⋮ → **Testlauf …** lädt ein Modell mit einem gewählten Kontext, lässt es eine feste Aufgabe beantworten
(eine TypeScript-Funktion, 128 Token, Temperatur 0, fester Seed) und misst dabei:

| Wert | Quelle |
|---|---|
| **Antwort** (tok/s) | Ollamas eigene Uhr: `eval_count / eval_duration` |
| **Prompt** (tok/s) | `prompt_eval_count / prompt_eval_duration` |
| **Laden** (s) | `load_duration`: Datei lesen, auf die GPU bringen |
| **Kontext** | was Ollama wirklich geladen hat (`/api/ps`). Weicht er ab, hat Ollama gekürzt. |
| **Speicher** | Größe und GPU-Anteil laut `/api/ps`. Das ist die Messung, die den Katalog kalibriert. |
| **Karte** | NVML vorher → nachher, und wie viel der Runner über Ollamas Zählung hinaus belegt |

Der Ablauf: Geladene Modelle entladen → leere Karte messen (zählt als „andere Programme“) → laden und
antworten → Speicher messen → wieder entladen. Danach ist die GPU so frei wie vorher.

Vor dem Start rechnet der Dialog für den gewählten Kontext vor, was es kostet:

- **passt**: Start frei.
- **knapp** oder **unklar**: Start nur mit Häkchen („Mir ist klar: …“).
- **Teil-Offload**: kein Start. Das ist genau der Fall, in dem der Runner abstürzt. Einen kleineren Kontext
  wählen.

Weitere Sperren: Es läuft immer nur ein Testlauf gleichzeitig, nicht während einer RAG-Indexierung (die
braucht das Einbettungsmodell auf der GPU), und nicht für Einbettungsmodelle. Läuft Ollama auf einem anderen
Rechner, sind Testläufe gesperrt: Ein Test entlädt das Chat-Modell von jemandem, der dort gerade arbeitet.
Der Dialog warnt außerdem vorab, welche geladenen Modelle der Test entlädt.

Die letzten fünf Testläufe stehen in den Details, der neueste auch in der Zeile.

### Details

⋮ → **Details …** zeigt alles zu einem Modell: Herleitung des Kontexts, die Schätzung als Rechnung (Formel
und kalibriert), die Testläufe, alle Beobachtungen (Kontext, Größe, Anteil auf der GPU, wie oft geladen) und
alle Parameter.

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
| `GET /api/ps` | alle 10 s (Pause während eines Testlaufs) | geladene Modelle mit Kontext und VRAM-Anteil (Messwerte) |
| `server.log` | bei jedem Einlesen (erstes MB der neuesten Datei) | Ollama-Standards |
| NVML | bei jedem Einlesen | GPU-Name und Größe (Hardware-Profil der Messwerte) |
| NVML | wenn `/api/ps` leer ist | belegter Speicher = andere Programme |
| `POST /api/generate` | nur im Testlauf | Laden, Antworten, Zeiten; mit `keep_alive: 0` zum Entladen |

Messwerte werden pro Modell-Digest × Kontext × GPU gespeichert. Ein neu gezogenes Modell (neuer Digest)
fängt also bei null an. Gespeichert wird alles in `data/control-center.db`: `catalog_models`,
`catalog_observations`, `catalog_benches` (Testläufe, die letzten 200) und `catalog_state` (andere
Programme). Ist Ollama nicht erreichbar, zeigt die Seite den letzten bekannten Stand.

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
other_usage_gib = 1.2            # andere Programme, bis zur ersten Messung
ollama_reserve_gib = 0.45        # hält Ollama pro GPU frei
tight_ratio = 0.9                # ab hier „knapp“ (Anteil am Budget)
allow_remote_tests = false       # Testläufe gegen ein Ollama auf einem anderen Rechner
test_num_predict = 128           # Token pro Testantwort
test_timeout_s = 300             # Laden von der Platte dauert bei großen Modellen
unload_after_test = true         # GPU danach wieder freigeben
keep_tests = 200                 # gespeicherte Testläufe (alle Modelle)
keep_removed_days = 90
```

Auf dem **Zweitrechner** kann das Control Center das Log des AI-Rechners nicht lesen und dessen GPU nicht
sehen. Die Ollama-Standards trägt man dort von Hand ein (Werte wie oben), dann rechnet die Schätzung wie am
AI-Rechner. Mit `[adapters] gpu = "fake"` nimmt der Katalog die GPU aus dem Fake-Szenario (aufgenommen am
AI-Rechner) und schreibt „GPU simuliert“ dazu. Ohne GPU bleibt die Prognose „unklar“. Testläufe sind dort
gesperrt (siehe oben). In der Simulation laufen sie mit Zeiten aus dem Fake-Adapter und ohne Speicherwerte.
Das Szenario `real-catalog` ist der Modellbestand des AI-Rechners vom 08.10. (12 Modelle, leere Karte).

### Fehlerbilder

| Anzeige | Ursache |
|---|---|
| „Keine Verbindung: …“ | Ollama läuft nicht oder `[adapters] ollama_url` stimmt nicht. Die Liste ist der letzte Stand. |
| „Nur Grunddaten – /api/show fehlgeschlagen“ | Das Modell wurde beim Einlesen gerade gelöscht, oder Ollama hing. Wird beim nächsten Einlesen wiederholt. |
| Ollama-Standards „unbekannt“ | Ollama läuft auf einem anderen Rechner, oder `server.log` hat (noch) keine „server config“-Zeile. Werte in `[modules.catalog]` eintragen. |
| „fake: ollama-tags.json missing“ | Simulation mit einem Szenario, das vor dem Katalog aufgenommen wurde. Szenario neu aufnehmen (`capture_samples`). |
| Testlauf „Ollama meldet einen Fehler: HTTP 500 …“ | Der Runner ist abgestürzt, meist Teil-Offload trotz passender Schätzung. Kleineren Kontext wählen, `server.log` ansehen. |
| Testlauf „Nach dem Antworten nicht in /api/ps gesehen“ | Ollama hat das Modell sofort wieder entladen (`keep_alive` 0 am Server?). Zeiten stimmen, Speicherwerte fehlen. |
| „Angefragt 131.072 Token, geladen mit 32.768“ | Ollama hat auf die Trainingslänge gekürzt. Die Messung gilt für den geladenen Wert. |
| Testlauf abgebrochen | Das Control Center wurde während des Laufs beendet. Das Modell bleibt eventuell geladen, bis `keep_alive` abläuft. |
| Menüpunkt „Testlauf“ grau | Maus darüber zeigt den Grund: anderer Rechner, Einbettungsmodell, Ollama aus, schon ein Lauf aktiv. |
