"""Family tree: declared parents, weights fallback, missing parents, cycles, differences."""
import pytest

from control_center.modules.catalog.lineage import build_lineage, changes, norm

from .factories import rec


@pytest.mark.parametrize("raw, key", [
    ("qwen3:8b", "qwen3:8b"), ("Qwen3:8B", "qwen3:8b"), ("qwen3", "qwen3:latest"),
    ("registry.ollama.ai/library/qwen3:8b", "qwen3:8b"), ("hf.co/org/model", "hf.co/org/model:latest"),
    ("hf.co/org/model:Q4_K_M", "hf.co/org/model:q4_k_m"),
])
def test_norm(raw, key):
    assert norm(raw) == key


def tree(records):
    links, groups = build_lineage(records)
    return links, [(g.origin, g.installed, g.members) for g in groups]


def test_declared_chain_two_levels():
    links, groups = tree([rec("code:latest", parent="ctx64k"), rec("ctx64k:latest", parent="base:9b"), rec("base:9b")])
    assert groups == [("base:9b", True, ["base:9b", "ctx64k:latest", "code:latest"])]
    assert (links["code:latest"].parent, links["code:latest"].via, links["code:latest"].depth) == (
        "ctx64k:latest", "declared", 2)


def test_weights_fallback_hangs_below_the_oldest_original():
    links, groups = tree([
        rec("imported:latest", weights="w", modified="2026-09-14T00:00:00+00:00"),
        rec("base:9b", weights="w", modified="2026-09-12T00:00:00+00:00"),
        rec("other:1", weights="x"),
    ])
    assert ("base:9b", True, ["base:9b", "imported:latest"]) in groups
    assert links["imported:latest"].via == "weights" and links["other:1"].parent is None


def test_weights_fallback_prefers_models_without_a_recorded_parent():
    # the derived one is older, but it has a (missing) parent - the pulled original is the anchor
    links, _ = tree([rec("derived:1", parent="gone:1", weights="w", modified="2026-01-01T00:00:00+00:00"),
                     rec("pulled:1", weights="w", modified="2026-09-01T00:00:00+00:00")])
    assert links["derived:1"].parent == "pulled:1" and links["derived:1"].via == "weights"


def test_missing_parent_becomes_a_virtual_origin_shared_by_its_children():
    links, groups = tree([rec("a-24k:latest", parent="deepseek-coder-v2:16b"),
                          rec("b-8k:latest", parent="deepseek-coder-v2:16b"), rec("solo:1")])
    assert ("deepseek-coder-v2:16b", False, ["a-24k:latest", "b-8k:latest"]) in groups
    assert links["a-24k:latest"].depth == 1 and links["a-24k:latest"].origin == "deepseek-coder-v2:16b"


def test_parent_spelled_differently_is_still_found():
    links, _ = tree([rec("child:1", parent="registry.ollama.ai/library/Base"), rec("base:latest")])
    assert links["child:1"].parent == "base:latest"


def test_self_parent_and_cycles_do_not_hang():
    links, groups = tree([rec("self:1", parent="self:1"), rec("a:1", parent="b:1"), rec("b:1", parent="a:1")])
    assert links["self:1"].parent is None
    members = sorted(m for _, _, ms in groups for m in ms)
    assert members == ["a:1", "b:1", "self:1"]                 # everyone shows up exactly once


def test_weights_link_never_closes_a_circle():
    # anchor (oldest, no parent) - would the weights link point back into its own ancestry? Never.
    links, groups = tree([rec("x:1", weights="w", modified="2026-01-01T00:00:00+00:00"),
                          rec("y:1", parent="x:1", weights="w")])
    assert links["y:1"].via == "declared" and links["x:1"].parent is None
    assert groups == [("x:1", True, ["x:1", "y:1"])]


def test_changes_against_the_parent():
    parent = rec("p:1", params={"num_ctx": ["4096"], "temperature": ["0.7"], "stop": ["a"]},
                 system_hash=None, template_hash="t", weights="w")
    child = rec("c:1", params={"num_ctx": ["65536"], "stop": ["b"]}, system_hash="s", system_chars=412,
                template_hash="t2", weights="w2")
    assert changes(child, parent) == ["num_ctx 65536", "stop-Wörter geändert", "temperature entfernt",
                                      "System-Prompt (412 Zeichen)", "Template geändert", "andere Gewichte"]
    assert changes(parent, parent) == []
    assert changes(rec("c:2", system_hash=None), rec("p:2", system_hash="s")) == ["System-Prompt entfernt"]


def test_weights_link_keeps_the_recorded_direction():
    # the derived model is OLDER (its parent was re-created later) and becomes the weights anchor;
    # hanging the parent below its own child would close a circle - the recorded direction wins
    links, groups = tree([
        rec("child:1", parent="parent:1", weights="w", modified="2026-01-01T00:00:00+00:00"),
        rec("parent:1", parent="gone:1", weights="w", modified="2026-09-01T00:00:00+00:00"),
    ])
    assert links["child:1"].parent == "parent:1" and links["child:1"].via == "declared"
    assert links["parent:1"].parent is None
    assert groups == [("gone:1", False, ["parent:1", "child:1"])]
