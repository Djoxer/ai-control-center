# Model library samples

Synthetic answers of the Ollama registry for the candidate check of the catalog, replayed by
`FakeLibrary` (`[adapters] library = "fake"`) and used by the tests. Not a scenario of the AI box: the
registry is outside of it, so one set serves every `fake_scenario`.

| Name | Shows |
|---|---|
| `qwen3-coder:30b` | MoE coder, 17.3 GiB of weights: splits at every context; tools via Ollama's parser (no template layer) |
| `qwen3.5:9b` | the weights digest of the installed `qwen3.5:9b` in `real-catalog` ("already installed"), separate vision projector, minimum Ollama version |
| `qwen3:14b` | fits, but trained on 40,960 tokens only: too short for OpenCode |
| `devstral:24b` | agent model with tools whose weights alone nearly fill the card |
| `gemma3:12b` | vision encoder inside the weights file, sliding window, template without tools |
| `gemma4:latest` | window plan per layer (bool list), shorter keys/values in window layers, KV sharing, separate projector |
| `qwen3-coder:480b-cloud` | cloud model: no weights |

Files: `index.json` (model name -> manifest file), `manifests/` (registry manifests), `blobs/sha256-<hex>`
(config, params, template and the first bytes of each weights file: a GGUF header with a shortened tokenizer).
Sizes and architectures follow the real models; digests are made up, except the qwen3.5 weights.

Generated - edit `backend/tests/fixtures/library_samples.py`, then run in `backend/`:

    uv run python tests/fixtures/library_samples.py

`tests/adapters/test_library.py` fails when the files and the generator disagree.
