# Katalog

Zeigt die Modelle, die in Ollama installiert sind: woraus sie gemacht wurden, mit welchem Kontext sie
laufen, ob sie komplett auf die GPU passen, wie schnell sie im eigenen Testlauf antworten, ob sie für OpenCode
taugen und wofür das Team sie einsetzt. Die zweite Ansicht, **Kandidaten**, prüft Modelle aus der Ollama-Bibliothek,
bevor sie jemand herunterlädt. Der alte Katalog (`katalog_server.py`, Port 8766) war eine Marktübersicht aus
BenchLM-Daten. Dieser hier fängt bei dem an, was auf dem AI-Rechner wirklich liegt. Die BenchLM-Daten kommen im
nächsten Schritt dazu.

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

### Einsatz

⋮ → **Einsatz …** hält fest, wofür das Team ein Modell nutzt. Die Markierungen stehen blau neben dem Namen:

| Markierung | Bedeutung |
|---|---|
| **OpenCode** | Coding-Agent im Editor oder Terminal |
| **OpenWebUI** | Chat im Browser, für das Team freigegeben |
| **RAG** | Einbettungen für die Codesuche. Setzt der Katalog von selbst für das `embedding_model` des RAG-Moduls. |
| **Test** | wird gerade ausprobiert |
| **Löschkandidat** | wird nicht mehr gebraucht, zählt nicht als „im Einsatz“ |

Dazu eine kurze Notiz (200 Zeichen), etwa „Standard für OpenCode, Coding-Sampling“. Die Angaben hängen am
Namen: Wird ein Modell neu gezogen, behält es seinen Einsatz. Der Filter über den Karten zeigt **Alle**,
**Im Einsatz** oder **Ohne Einsatz** – Letzteres ist die Aufräumliste.

### OpenCode-Eignung

OpenCode braucht drei Dinge. Der Katalog prüft jedes mit dem besten Beleg, den er hat:

1. **Strukturierte Tool-Calls** – gemessen im Testlauf (siehe unten). Das Ollama-Etikett „Tools“ reicht nicht:
   qwen2.5-coder trägt es und schreibt seine Tool-Calls trotzdem als Text.
2. **Genug Kontext** – mindestens `opencode_min_context` (65.536 Token). Systemprompt, Werkzeugbeschreibungen
   und gelesene Dateien füllen ihn schnell. Ist der wirksame Kontext kleiner, rechnet der Katalog vor, ob eine
   Variante mit `num_ctx 65536` auf die Karte passen würde.
3. **Passt auf die Karte** – die Prognose beim wirksamen Kontext.

In der Zeile steht dann **OpenCode ✓**, **OpenCode ?** (eingeschränkt) oder **OpenCode ✗**; ohne Tool-Test
steht dort noch nichts. Die Details nennen die Gründe und liefern den fertigen Eintrag für die `opencode.json`
(unter `provider.ollama.models`), mit Knopf zum Kopieren:

```json
"qwen3.5-9b-64k-code": {
  "name": "qwen3.5-9b-64k-code",
  "limit": { "context": 65536, "output": 8192 }
}
```

Das `limit.context` ist der wichtige Teil. Ohne Angabe nimmt OpenCode für unbekannte Modelle ein viel größeres
Fenster an (die neue OpenCode-Doku nennt 200.000 Token). Ollama schneidet alles über `num_ctx` stillschweigend
ab – oft den Anfang des Gesprächs mit den Regeln. `limit.output` ist ein Viertel des Kontexts, höchstens
`opencode_output_tokens` (8.192).

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
zeigt. Steht daneben grau **+0,8**, belegt die Karte so viel mehr, als Ollama zählt (gemessen im Testlauf,
siehe „Was Ollama nicht zählt“). Der Balken vergleicht beides zusammen mit dem **GPU-Budget**: dem Teil der
Karte, der für Ollama übrig bleibt. Die Maus über dem Balken zeigt die Rechnung.

Die Farbe sagt, woher die Zahl kommt:

- **grün, „gemessen“**: Ollama hat das Modell mit genau diesem Kontext auf dieser GPU geladen, und der
  Katalog hat es in `/api/ps` gesehen, oder ein Testlauf hat es gemessen. Das schlägt jede Schätzung.
- **gelb mit ≈, „geschätzt“**: aus den Modelldaten berechnet, bevor das Modell je so geladen wurde.
  Gewichte (Dateigröße) + KV-Cache (Schichten × KV-Köpfe × Kopfgröße × Kontext × KV-Typ) + 0,4 GiB
  Rechenpuffer. Schichten mit Sliding Window speichern nur ihr Fenster; den Plan liefert das GGUF selbst
  (`sliding_window_pattern`, z. B. gemma4: 5 Fenster-Schichten, dann eine mit voller Länge) oder der Katalog
  kennt ihn für die Architektur (gemma2/3, gpt-oss, cohere2). Teilen Schichten ihren KV-Cache (gemma4), rechnet
  der Katalog trotzdem jede Schicht – die sichere Seite.
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

### Was Ollama nicht zählt

Ollamas Größe in `/api/ps` ist nicht alles, was der Runner auf der Karte belegt. Jeder Testlauf misst den Rest:
Karte nachher − Karte vorher − Ollamas Zählung. Am AI-Rechner (08.10.):

| Modell | Ollama zählt | Karte belegt mehr |
|---|---|---|
| qwen2.5-coder:14b, deepseek-coder-v2:16b | 11,3 / 10,5 GiB | 0,2 GiB |
| qwen3.5-9b-64k-code (mit Bild-Encoder) | 6,7 GiB | 1,2 GiB |

Die Reserve (0,45 GiB) deckt die 0,2 GiB der Textmodelle. Was darüber liegt, rechnet der Katalog zum Bedarf
dazu, für alle Modelle mit denselben Gewichten: bei qwen3.5 1,2 − 0,45 ≈ **+0,8 GiB**. Die Details sagen, aus
welchem Testlauf die Zahl stammt.

Ohne Testlauf weiß der Katalog das nicht. Für Bildmodelle steht dann in den Details „Bild-Encoder: fehlt in
dieser Zahl“. Die 90-%-Grenze für „knapp“ lässt bei 14 GiB Budget rund 1,4 GiB Luft. Das deckt den Encoder
meist ab, aber nur ein Testlauf zeigt es sicher.

Ein Modell, dessen Zählung passt, dessen Zählung plus Rest aber nicht, gilt als „Teil-Offload“: Ollama lädt es
komplett, die Karte läuft trotzdem über.

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
(eine TypeScript-Funktion, 128 Token, Temperatur 0, fester Seed), prüft drei Tool-Calls und misst dabei:

| Wert | Quelle |
|---|---|
| **Antwort** (tok/s) | Ollamas eigene Uhr: `eval_count / eval_duration` |
| **Prompt** (tok/s) | `prompt_eval_count / prompt_eval_duration` |
| **Laden** (s) | `load_duration`: Datei lesen, auf die GPU bringen |
| **Kontext** | was Ollama wirklich geladen hat (`/api/ps`). Weicht er ab, hat Ollama gekürzt. |
| **Speicher** | Größe und GPU-Anteil laut `/api/ps`. Das ist die Messung, die den Katalog kalibriert. |
| **Karte** | NVML vorher → nachher, und wie viel der Runner über Ollamas Zählung hinaus belegt (geht in die Prognose ein, siehe oben) |

Der Ablauf: Geladene Modelle entladen → leere Karte messen (zählt als „andere Programme“) → laden und
antworten → Speicher messen → Tool-Calls prüfen → wieder entladen. Danach ist die GPU so frei wie vorher.

**Tool-Calls:** drei kleine Aufträge über `/api/chat`, mit Werkzeugbeschreibungen, wie ein Coding-Agent sie
schickt. Gleicher Kontext wie der Lauf, also kein Neuladen:

| Auftrag | Prüft |
|---|---|
| **Datei lesen** – „Zeig mir den Inhalt der Datei src/app/app.config.ts“ | ein Werkzeug, ein Argument |
| **Werkzeug wählen** – „Starte die Unit-Tests mit npm test“ (Datei lesen oder Befehl ausführen) | richtige Wahl unter zweien |
| **Argumente mit Typen** – „Suche nach TODO in src, höchstens 5 Treffer“ | Zahl als Zahl, nicht als Text |

Bestanden heißt: Ollama liefert einen echten `tool_calls`-Eintrag mit dem richtigen Werkzeug und passenden
Argumenten. Typische Fehler stehen im Ergebnis: „Tool-Call nur als Text“, „Falsches Werkzeug“, „max_results =
'5' (Text statt Zahl)“, „Abgebrochen … (denkt zu lange)“. Ohne Tool-Format im Template lehnt Ollama ab – dann
steht dort „nicht geprüft“. Denkende Modelle (qwen3.5) dürfen vorher bis zu `test_tool_num_predict` (2.048)
Token überlegen.

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

### Kandidaten: vor dem Download prüfen

Ein Pull von `qwen3-coder:30b` lädt 17 GB, nur um dann festzustellen, dass das Modell nicht auf die Karte
passt. Die Ansicht **Kandidaten** stellt diese Frage vorher. Man tippt den Namen ein wie bei `ollama pull`
(`qwen3-coder:30b`, `gemma3:12b`, `nutzer/modell:tag`, `hf.co/organisation/repo:Q4_K_M`). Ein ganzer Befehl
(`ollama pull …`) oder die Adresse der Modellseite auf ollama.com oder huggingface.co geht auch.

**Ollama-Bibliothek** (rechts neben dem Umschalter) öffnet die Suche von ollama.com in einem neuen Tab und
schaltet hier schon auf „Kandidaten“. Dort ein Modell suchen, den Namen samt Tag (Seite des Modells → „Tags“)
oder einfach die Adresse der Modellseite kopieren und hier einfügen.

Der Katalog fragt die Registry dasselbe wie Ollama beim Pull, hört aber früh auf:

1. **Manifest**: welche Dateien, wie groß. Gewichte, Bild-Encoder (`projector`), Template, Parameter.
2. **Kleine Dateien**: Konfiguration (Familie, Größe, Quantisierung, Ollamas Parser, Mindestversion),
   Parameter (`num_ctx`, `stop` …), Template.
3. **Anfang der Gewichtsdatei**: die GGUF-Metadaten mit Schichten, KV-Köpfen, Kopfgröße und
   Trainingslänge. Sie stehen ganz vorne in der Datei, meist in den ersten 1–8 MiB (die Tokenizer-Tabellen
   davor sind der größte Teil). Gelesen wird per Range-Anfrage, Stück für Stück, höchstens
   `registry_header_max_mib`.

Daraus baut der Katalog denselben Datensatz wie für ein installiertes Modell. Deshalb gelten dieselbe Formel,
dasselbe GPU-Budget, dieselbe Kalibrierung und dieselbe OpenCode-Prüfung. Liegt dieselbe Gewichtsdatei schon auf
der Platte (gleicher Blob, etwa weil eine Variante installiert ist), rechnet der Kandidat mit deren Messungen.

Die Karte eines Kandidaten zeigt:

- **Download**: alle Dateien zusammen, so groß wie später in der Modellliste. Bei Bildmodellen steht der
  Bild-Encoder extra dabei.
- **Kontext und VRAM-Bedarf** beim wirksamen Kontext, also mit den Ollama-Standards oder dem `num_ctx` des
  Modells.
- **Bedarf nach Kontextlänge**: eine Pille je Stufe von 2k bis zur Trainingslänge. ✓ heißt passt, ! heißt knapp,
  ✗ heißt Teil-Offload. Die Maus über einer Pille zeigt den Bedarf. Darunter steht der Satz, bis zu welchem
  Kontext das Modell passt. Das ist der Wert für ein `num_ctx` in einer eigenen Variante.
- **OpenCode**: Kontext und Karte wie bei installierten Modellen. Ob ein Modell Tool-Calls sauber strukturiert,
  zeigt erst ein Testlauf nach dem Download. Vorher sagt die Registry nur, ob das Template Werkzeuge kennt
  (`.Tools`) oder ob Ollama einen eingebauten Parser dafür hat. Kennt das Template keine, lehnt Ollama
  Tool-Anfragen später ab. Dann steht der Kandidat schon jetzt auf ✗.
- **Hinweise**: „Schon installiert“ (gleiche Gewichte), „Installiert ist ein anderer Stand“ (gleicher Name,
  andere Gewichte), „Die Gewichte liegen schon auf der Platte“ (ein Pull lädt nur den Rest). „Braucht Ollama ≥ …“
  erscheint gelb, wenn die Konfiguration des Modells eine neuere Ollama-Version verlangt als installiert ist.
- **Ungenau** (blauer Hinweis): Die Architektur passt nicht ganz in die Formel (z. B. ein Sliding-Window-Plan, den
  der Katalog nicht kennt). Gerechnet ist dann die sichere Seite, also eher zu viel.
- **Rechnung und Modelldaten** (aufklappen): die Hinweise der Schätzung und alle gelesenen GGUF-Metadaten. Damit
  lässt sich nachvollziehen, womit die Formel gerechnet hat.
- **`ollama pull …`** zum Kopieren.

Die Fähigkeiten (Tools, Bild, Denkt) leitet der Katalog so ab, wie Ollama es tut. Endgültig sind sie erst nach
dem Download. Cloud-Modelle (`…-cloud`) laufen bei ollama.com und haben keine Gewichte, die Karte sagt das.
Geprüfte Kandidaten bleiben gespeichert, die Prognose rechnet bei jedem Aufruf neu (neue Messungen, anderes
Budget). ⋮ → **Neu prüfen** fragt die Registry noch einmal, ⋮ → **Aus der Liste nehmen** löscht den Eintrag.
Heruntergeladen wird nichts. Den Pull macht man selbst.

### Details

⋮ → **Details …** zeigt alles zu einem Modell: Einsatz, OpenCode-Eignung mit `opencode.json`-Eintrag,
Herleitung des Kontexts, die Schätzung als Rechnung (Formel und kalibriert), die Testläufe, alle Beobachtungen
(Kontext, Größe, Anteil auf der GPU, wie oft geladen) und alle Parameter.

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
| `POST /api/chat` | nur im Testlauf (3×) | Tool-Calls mit Werkzeugbeschreibungen |
| Registry (`registry.ollama.ai`, `hf.co`) | nur beim Prüfen eines Kandidaten | Manifest, Konfiguration, Parameter, Template, Anfang der Gewichtsdatei |

Messwerte werden pro Modell-Digest × Kontext × GPU gespeichert. Ein neu gezogenes Modell (neuer Digest)
fängt also bei null an. Gespeichert wird alles in `data/control-center.db`: `catalog_models`,
`catalog_observations`, `catalog_benches` (Testläufe samt Tool-Calls, die letzten 200), `catalog_state`
(andere Programme), `catalog_usage` (Einsatz und Notiz, nach Modellname) und `catalog_candidates` (was die
Registry über geprüfte Kandidaten sagte). Ist Ollama nicht erreichbar, zeigt die Seite den letzten bekannten Stand.

Der Server spricht nur mit den Registries aus `[adapters] library_hosts`. Der Name kommt aus einem Textfeld
der Seite, und das Control Center soll keine beliebigen Adressen im LAN abrufen. Eine Registry braucht
Internetzugang vom AI-Rechner (Ollama selbst braucht ihn für jeden Pull ohnehin). Ein Proxy aus den
Umgebungsvariablen `HTTPS_PROXY`/`HTTP_PROXY` wird benutzt. `[adapters] library = "fake"` antwortet stattdessen
aus `adapters/library-samples` (synthetische Kandidaten für Tests und den Zweitrechner ohne Internet).

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
test_tools = true                # Tool-Calls im Testlauf prüfen
test_tool_num_predict = 2048     # Spielraum für denkende Modelle
opencode_min_context = 65536     # Kontext, den OpenCode mindestens braucht
opencode_output_tokens = 8192    # limit.output im opencode.json-Eintrag (höchstens ein Viertel des Kontexts)
registry_timeout_s = 20          # Kandidaten: pro Anfrage an die Registry
registry_header_max_mib = 32     # höchstens so viel der Gewichtsdatei lesen
keep_candidates = 30             # gespeicherte Kandidaten, die ältesten fallen heraus
keep_removed_days = 90
```

Und in `[adapters]`, für die Kandidaten:

```toml
library = "http"                 # http | fake
library_hosts = ["registry.ollama.ai", "hf.co"]
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
| Tool-Calls „Abgebrochen nach 2048 Token … (denkt zu lange)“ | Das Modell überlegt länger als erlaubt. `test_tool_num_predict` erhöhen – oder es ist für einen Agenten zu langsam. |
| Tool-Calls „Ollama meldet einen Fehler …“ in allen drei Fällen | Der Runner ist während der Prüfung abgestürzt. Tempo und Speicher davor sind trotzdem gemessen. |
| Kandidat „… gibt es in registry.ollama.ai nicht“ | Name oder Tag falsch (Tags stehen auf der Modellseite unter „Tags“), oder das Modell ist privat. |
| Kandidat „Registry nicht lesbar: … nicht erreichbar“ | Kein Internet am AI-Rechner, Proxy fehlt, oder die Registry hängt. `registry_timeout_s` erhöhen hilft nur bei langsamen Leitungen. |
| Kandidat „Registry „…“ ist nicht freigegeben“ | Der Host steht nicht in `[adapters] library_hosts`. Eintragen, wenn man ihm traut. |
| Kandidat „Metadaten nur zum Teil gelesen“ | Der GGUF-Kopf ist größer als `registry_header_max_mib`. Grenze erhöhen, wenn Angaben fehlen. |
| Kandidat „Keine GGUF-Datei“ / „Format … liest nur GGUF“ | Das Modell liegt in einem anderen Format vor (z. B. safetensors). Ohne GGUF-Metadaten keine Formel. |
