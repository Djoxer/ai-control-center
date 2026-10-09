"""Records from Ollama answers, JSON round trip, server defaults from server.log."""
import os
import time
from datetime import datetime, timezone

from control_center.modules.catalog.collector import (
    ModelRecord, ServerDefaults, parse_server_config, read_server_config, record_from,
)

from .factories import details, tag

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
LINE = ('time=2026-10-08T07:58:01.123+02:00 level=INFO source=routes.go:1301 msg="server config" '
        'env="map[CUDA_VISIBLE_DEVICES: HTTPS_PROXY: OLLAMA_CONTEXT_LENGTH:65536 OLLAMA_DEBUG:INFO '
        'OLLAMA_FLASH_ATTENTION:true OLLAMA_GPU_OVERHEAD:0 OLLAMA_HOST:http://0.0.0.0:11434 '
        'OLLAMA_KEEP_ALIVE:5m0s OLLAMA_KV_CACHE_TYPE:q8_0 OLLAMA_MAX_LOADED_MODELS:1 '
        'OLLAMA_MODELS:C:\\\\Users\\\\jemand\\\\.ollama\\\\models OLLAMA_NUM_PARALLEL:1 '
        'OLLAMA_ORIGINS:[http://localhost https://localhost app://*] ROCR_VISIBLE_DEVICES:]"')


def test_record_from_tags_and_show():
    info = {"general.architecture": "qwen2", "qwen2.block_count": 48, "tokenizer.ggml.tokens": None,
            "per.layer": [8, 0, 8], "long.list": list(range(600)), "names": ["a", "b"]}
    r = record_from(tag("coder:14b", parent="base:1"), details("coder:14b", parent=None, num_ctx=8192, info=info), NOW)
    assert r.parent_model == "base:1"                  # tags knows the parent although show says nothing
    assert r.parameters == {"num_ctx": ["8192"]} and r.weights_digest == "w1" and r.collected_at == NOW.isoformat()
    # scalars and short number lists stay; nulls, long lists and string lists go
    assert r.model_info == {"general.architecture": "qwen2", "qwen2.block_count": 48, "per.layer": [8, 0, 8]}
    assert r.architecture == "qwen2" and r.info("block_count") == 48 and r.param("num_ctx") == "8192"


def test_record_without_show_keeps_tags_data():
    r = record_from(tag("x:1", size=42), None, NOW, show_error="kaputt")
    assert (r.size, r.show_error, r.parameters, r.collected_at, r.architecture) == (42, "kaputt", {}, None, None)


def test_json_round_trip_tolerates_old_and_new_fields():
    r = record_from(tag("x:1"), details("x:1", num_ctx=4096), NOW)
    assert ModelRecord.from_json(r.to_json()) == r
    data = r.to_json()
    data["field_from_the_future"] = 1
    del data["capabilities"]                           # row written before the field existed
    back = ModelRecord.from_json(data)
    assert back.capabilities == [] and back.name == "x:1"


def test_parse_server_config_line():
    s = parse_server_config(LINE)
    assert s == ServerDefaults(context_length=65536, kv_cache_type="q8_0", flash_attention=True, num_parallel=1,
                               max_loaded_models=1, keep_alive="5m0s")


def test_parse_server_config_defaults_and_gaps():
    s = parse_server_config('msg="server config" env="map[OLLAMA_CONTEXT_LENGTH:0 OLLAMA_KV_CACHE_TYPE: '
                            'OLLAMA_FLASH_ATTENTION: OLLAMA_NUM_PARALLEL:0]"')
    # 0 = Ollama decides itself (unknown to us), empty KV type = f16, parallel 0 = automatic = 1
    assert (s.context_length, s.kv_cache_type, s.flash_attention, s.num_parallel) == (None, "f16", None, 1)
    assert parse_server_config('msg="server config" env="map[]"') == ServerDefaults()


def _log(path, lines, age_s=0.0):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    t = time.time() - age_s
    os.utime(path, (t, t))


def test_read_server_config_newest_file_first(tmp_path):
    _log(tmp_path / "server-1.log", [LINE.replace("65536", "8192")], age_s=100)
    _log(tmp_path / "server.log", ["noise", LINE, "more noise"])
    values, name = read_server_config([str(tmp_path / "server*.log")])
    assert values.context_length == 65536 and name == "server.log"     # file name only, never the folder


def test_read_server_config_falls_back_to_the_previous_file(tmp_path):
    _log(tmp_path / "server-1.log", [LINE], age_s=100)
    _log(tmp_path / "server.log", ["started without the line yet"])
    assert read_server_config([str(tmp_path / "server*.log")])[1] == "server-1.log"


def test_read_server_config_nothing(tmp_path):
    assert read_server_config([str(tmp_path / "server*.log")]) is None
    _log(tmp_path / "server.log", ["x"] * 3)
    assert read_server_config([str(tmp_path / "server*.log")]) is None


def test_read_server_config_only_reads_the_head(tmp_path):
    big = tmp_path / "server.log"
    big.write_text("x" * (1024 * 1024 + 10) + "\n" + LINE + "\n", encoding="utf-8")
    assert read_server_config([str(big)]) is None      # beyond the first MB: not worth reading a huge log


def test_keep_info_keeps_short_number_and_flag_lists():
    from control_center.modules.catalog.collector import keep_info
    info = {"a.block_count": 4, "a.attention.sliding_window_pattern": [True, False, True, False],
            "a.mixed": [1, "x"], "tokenizer.ggml.tokens": None, "a.long": list(range(600)), "a.name": "x"}
    assert keep_info(info) == {"a.block_count": 4, "a.attention.sliding_window_pattern": [True, False, True, False],
                               "a.name": "x"}
