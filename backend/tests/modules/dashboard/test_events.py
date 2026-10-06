"""Event detection: snapshot diffs, the restart window, quiet models, crash lines."""
from datetime import datetime, timedelta, timezone

from control_center.modules.dashboard.events import CrashWatcher, EventDetector
from control_center.modules.dashboard.schemas import DashboardSnapshot, LoadedModel
from control_center.modules.dashboard.settings import DashboardSettings

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
CFG = DashboardSettings(restart_window_s=30)


def m(name="coder:14b", ctx=8192, placement="gpu", ratio=1.0, digest="d1") -> LoadedModel:
    return LoadedModel(name=name, digest=digest, family=None, parameter_size=None, quantization=None,
                       size_bytes=100, vram_bytes=int(100 * ratio), gpu_ratio=ratio, placement=placement,
                       context_length=ctx, expires_at=None, pinned=False, unloading=False)


def snap(t: float, models=(), online=True, version="0.12.6") -> DashboardSnapshot:
    return DashboardSnapshot(ts=T0 + timedelta(seconds=t), node_id="box", simulated=[], ollama_online=online,
                             ollama_version=version, models=list(models), gpu=None, host=None, disks=None,
                             services=[], warnings=[], errors=[])


def run(*snaps, cfg=CFG):
    d = EventDetector(cfg)
    out = []
    for s in snaps:
        out += [(e.kind, e.level, e.subject) for e in d.observe(s)]
    return out, d


def test_first_snapshot_is_the_baseline_not_an_event():
    events, _ = run(snap(0, [m()]))
    assert events == []


def test_load_reload_unload():
    events, _ = run(snap(0), snap(2, [m(ctx=8192)]), snap(4, [m(ctx=32768)]), snap(6, [m(ctx=32768, digest="d2")]),
                    snap(8))
    assert [e[0] for e in events] == ["model_loaded", "model_reloaded", "model_reloaded", "model_unloaded"]


def test_offload_start_and_end():
    events, _ = run(snap(0, [m()]), snap(2, [m(placement="split", ratio=0.84)]), snap(4, [m()]),
                    snap(6, [m(placement="cpu", ratio=0.0)]))
    assert events == [("offload_started", "critical", "coder:14b"), ("offload_ended", "info", "coder:14b"),
                      ("offload_started", "warning", "coder:14b")]


def test_model_that_loads_split_gets_both_events():
    events, _ = run(snap(0), snap(2, [m(placement="split", ratio=0.5)]))
    assert [e[0] for e in events] == ["model_loaded", "offload_started"]


def test_quiet_model_is_counted_not_listed():
    emb = m("nomic-embed-text:latest")
    events, d = run(snap(0), snap(2, [emb]), snap(4), snap(6, [emb]))
    assert events == []
    assert d.quiet.loads["nomic-embed-text:latest"] == 2


def test_short_outage_is_one_restart():
    events, _ = run(snap(0, [m()]), snap(2, online=False), snap(14, online=False), snap(16, online=True))
    assert [e[0] for e in events] == ["ollama_restarted"]       # and no "unloaded" for the lost model


def test_long_outage_is_down_then_up():
    d = EventDetector(CFG)
    d.observe(snap(0))
    d.observe(snap(2, online=False))
    assert d.observe(snap(20, online=False)) == []
    down = d.observe(snap(40, online=False))
    assert [(e.kind, e.ts) for e in down] == [("ollama_down", T0 + timedelta(seconds=2))]   # dated to the start
    assert d.observe(snap(42, online=False)) == []                                         # reported once
    up = d.observe(snap(100))
    assert [e.kind for e in up] == ["ollama_up"] and "98 s" in up[0].message


def test_ollama_down_at_startup_coming_up_is_up_not_restart():
    events, _ = run(snap(0, online=False), snap(2))
    assert [e[0] for e in events] == ["ollama_up"]


def test_version_change():
    events, _ = run(snap(0), snap(2, version="0.12.7"))
    assert events == [("ollama_updated", "info", None)]


def test_crash_watcher_folds_a_burst_into_one_event(tmp_path):
    log = tmp_path / "server.log"
    log.write_text('time=x level=ERROR msg="CUDA error: out of memory" (old crash)\n')
    clock = [100.0]
    w = CrashWatcher(DashboardSettings(ollama_log_paths=[str(tmp_path / "server*.log")], crash_cooldown_s=30),
                     enabled=True, clock=lambda: clock[0])
    w.attach()
    assert w.watching == "server.log" and w.poll() == []         # old crash is history
    with log.open("a") as f:
        f.write("ggml_cuda_init: found 1 CUDA devices\n")
        f.write("CUDA error: an illegal memory access was encountered\n")
        f.write("Exception 0xc0000409 0x8 0x0 0x0\n")
    assert w.poll() == ["CUDA error: an illegal memory access was encountered"]
    assert w.suppressed == 1
    clock[0] += 31
    with log.open("a") as f:
        f.write("CUDA error: again\n")
    assert w.poll() == ["CUDA error: again"]


def test_crash_watcher_off_for_remote_ollama(tmp_path):
    (tmp_path / "server.log").write_text("x\n")
    w = CrashWatcher(DashboardSettings(ollama_log_paths=[str(tmp_path / "server.log")]), enabled=False)
    w.attach()
    assert w.watching is None and w.poll() == []
