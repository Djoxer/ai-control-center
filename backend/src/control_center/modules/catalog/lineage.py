"""Which installed model was made from which - the family tree of the catalog.

Think of it like a family register with two kinds of proof:
1. The birth certificate: "ollama create" writes the source model into details.parent_model.
   If that model is still installed, the link is certain ("declared").
0. Identical twins: the same manifest digest under two names is a copy ("ollama cp"). The newer name
   hangs below the older one as "Kopie von".
2. The DNA test: the weights blob (sha256 in the Modelfile FROM line). A model whose parent was
   deleted, or that was created straight from the GGUF file, still shares its weights with its
   relatives ("weights"). It hangs below the oldest model with the same weights that has no
   recorded parent itself - usually the one pulled from the library.

A model whose recorded parent is gone and that has no living relative becomes a root of its own,
grouped under the name of the missing parent ("deepseek-coder-v2:16b - nicht installiert").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from control_center.modules.catalog.collector import ModelRecord

Via = Literal["declared", "weights", "copy"]
REGISTRY_PREFIXES = ("registry.ollama.ai/library/", "registry.ollama.ai/")


def norm(name: str) -> str:
    """Ollama spellings of the same model -> one key: 'Qwen3:8B', 'registry.ollama.ai/library/qwen3:8b'."""
    n = name.strip().lower()
    for prefix in REGISTRY_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    last = n.rsplit("/", 1)[-1]
    return n if ":" in last else n + ":latest"


@dataclass
class Link:
    parent: str | None = None          # installed parent (its real name), None = root
    via: Via | None = None
    origin: str = ""                   # group key: name of the root, or of the missing parent
    depth: int = 0                     # 0 = origin itself; members of a missing origin start at 1


@dataclass
class Group:
    origin: str
    installed: bool                    # False: the origin is a parent that is not installed (any more)
    members: list[str] = field(default_factory=list)    # tree order: parent before its children


def _sort_key(r: ModelRecord) -> tuple:
    # oldest first: the model pulled first is the most likely original; name breaks ties
    return (r.modified_at or "9999", r.name.lower())


def build_lineage(records: list[ModelRecord]) -> tuple[dict[str, Link], list[Group]]:
    by_key = {norm(r.name): r for r in records}
    links = {r.name: Link() for r in records}

    def declared(r: ModelRecord) -> str | None:
        if not r.parent_model:
            return None
        key = norm(r.parent_model)
        return None if key == norm(r.name) else key

    # 0) identical twins: same manifest digest = "ollama cp", the oldest name is the original
    by_digest: dict[str, list[ModelRecord]] = {}
    for r in records:
        if r.digest:
            by_digest.setdefault(r.digest, []).append(r)
    for twins in by_digest.values():
        if len(twins) > 1:
            original = sorted(twins, key=_sort_key)[0]
            for r in twins:
                if r is not original:
                    links[r.name].parent, links[r.name].via = original.name, "copy"

    # 1) birth certificates
    for r in records:
        key = declared(r)
        if links[r.name].via is None and key and key in by_key:
            links[r.name].parent, links[r.name].via = by_key[key].name, "declared"

    def ancestors(name: str) -> set[str]:
        seen: set[str] = set()
        cur = links[name].parent
        while cur is not None and cur not in seen:
            seen.add(cur)
            cur = links[cur].parent
        return seen

    # 2) DNA test for everything still without a parent
    by_weights: dict[str, list[ModelRecord]] = {}
    for r in records:
        if r.weights_digest:
            by_weights.setdefault(r.weights_digest, []).append(r)
    for relatives in by_weights.values():
        if len(relatives) < 2:
            continue
        # anchor = a model without any recorded parent (pulled original) if there is one, oldest first
        anchor = sorted(relatives, key=lambda r: (declared(r) is not None, *_sort_key(r)))[0]
        for r in relatives:
            link = links[r.name]
            if r is anchor or link.parent is not None:
                continue
            if r.name in ancestors(anchor.name):
                continue                                # would close a circle
            link.parent, link.via = anchor.name, "weights"

    # cycles in recorded parents (should never happen, but a broken manifest must not hang us)
    for r in records:
        if r.name in ancestors(r.name):
            links[r.name].parent = links[r.name].via = None

    # 3) groups: every root is an origin, or hangs below the name of its missing parent
    children: dict[str, list[ModelRecord]] = {}
    roots: list[ModelRecord] = []
    for r in sorted(records, key=lambda r: r.name.lower()):
        parent = links[r.name].parent
        if parent is None:
            roots.append(r)
        else:
            children.setdefault(parent, []).append(r)

    groups: dict[str, Group] = {}

    def walk(r: ModelRecord, group: Group, depth: int) -> None:
        link = links[r.name]
        link.origin, link.depth = group.origin, depth
        group.members.append(r.name)
        for child in children.get(r.name, []):
            walk(child, group, depth + 1)

    for r in roots:
        missing = declared(r)
        if missing is not None and missing not in by_key:   # recorded parent not installed
            g = groups.setdefault("?" + missing, Group(origin=missing, installed=False))
            walk(r, g, 1)
        else:
            g = groups.setdefault(r.name, Group(origin=r.name, installed=True))
            walk(r, g, 0)
    ordered = sorted(groups.values(), key=lambda g: g.origin.lower())
    return links, ordered


def changes(child: ModelRecord, parent: ModelRecord) -> list[str]:
    """What a derived model sets differently than its parent, as short German labels."""
    out: list[str] = []
    for key in sorted(set(child.parameters) | set(parent.parameters)):
        mine, theirs = child.parameters.get(key), parent.parameters.get(key)
        if mine == theirs:
            continue
        if mine is None:
            out.append(f"{key} entfernt")
        elif key == "stop":
            out.append("stop-Wörter geändert")
        else:
            out.append(f"{key} {', '.join(mine)}")
    if child.system_hash != parent.system_hash:
        out.append(f"System-Prompt ({child.system_chars} Zeichen)" if child.system_hash else "System-Prompt entfernt")
    if child.template_hash != parent.template_hash and child.template_hash and parent.template_hash:
        out.append("Template geändert")
    if child.weights_digest and parent.weights_digest and child.weights_digest != parent.weights_digest:
        out.append("andere Gewichte")
    return out
