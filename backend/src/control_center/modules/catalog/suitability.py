"""OpenCode suitability: three questions, each answered by the best evidence the catalog has.

1. Does the model make STRUCTURED tool calls?  -> measured by the tool-call check of a test run
2. Is its context big enough for an agent?      -> effective context vs. opencode_min_context (64k: OpenCode's
                                                   system prompt, tool definitions and the files it reads add up)
3. Does that fit on the card?                   -> the verdict (measured, calibrated or estimated)

Plus the entry for opencode.json with the right limit.context: without it OpenCode assumes a huge window and
Ollama silently cuts whatever goes beyond num_ctx - often the start of the conversation with the rules.
"""
from __future__ import annotations

from datetime import datetime

from control_center.modules.catalog.estimate import ContextResult
from control_center.modules.catalog.estimate import Verdict as Judgement
from control_center.modules.catalog.schemas import FitState, OpencodeFit, ToolCheck
from control_center.modules.catalog.settings import CatalogSettings

SUMMARY: dict[FitState, str] = {
    "fits": "Geeignet für OpenCode",
    "maybe": "Eingeschränkt geeignet",
    "no": "Nicht geeignet für OpenCode",
    "unknown": "Noch offen: Tool-Calls ungeprüft",
}


def _num(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _gib(value: int | None) -> str:
    return "?" if value is None else f"{value / 1024 ** 3:.1f}".replace(".", ",") + " GiB"


def config_key(name: str) -> str:
    """Ollama accepts a name without ':latest' - that is also how people write it in opencode.json."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def opencode_block(name: str, context: int, cfg: CatalogSettings) -> str:
    """Entry for provider.ollama.models in opencode.json (schema of opencode.json as of 10/2026, v1)."""
    key = config_key(name)
    output = min(cfg.opencode_output_tokens, max(256, context // 4))
    return (f'"{key}": {{\n'
            f'  "name": "{key}",\n'
            f'  "limit": {{ "context": {context}, "output": {output} }}\n'
            f'}}')


def opencode_fit(name: str, ctx: ContextResult, now: Judgement, at_min: tuple[ContextResult, Judgement] | None,
                 tools: ToolCheck | None, tools_at: datetime | None, eval_tps: float | None,
                 cfg: CatalogSettings, to_schema, tool_capability: bool = True) -> OpencodeFit:
    """now = verdict at the effective context; at_min = (context, verdict) when asked for opencode_min_context,
    only computed when the effective context is smaller. to_schema converts a Judgement for the API.
    tool_capability = Ollama lists "tools" for the model: without it no test is needed to say no."""
    minimum = cfg.opencode_min_context
    reasons: list[str] = []
    no = maybe = untested = False

    # 1) tool calls
    sim = " (Simulation)" if tools is not None and tools.simulated else ""
    if tools is None and not tool_capability:
        no = True
        reasons.append("Ollama meldet für dieses Modell keine Tool-Unterstützung.")
    elif tools is None:
        untested = True
        reasons.append("Tool-Calls noch nicht geprüft – ein Testlauf prüft sie mit.")
    elif tools.skipped:
        no = True
        reasons.append(tools.skipped)
    else:
        failed = next((c for c in tools.cases if not c.ok), None)
        if tools.passed == tools.total:
            reasons.append(f"Tool-Calls: {tools.passed}/{tools.total} strukturiert{sim}.")
        elif tools.passed > 0:
            maybe = True
            reasons.append(f"Tool-Calls: nur {tools.passed}/{tools.total}{sim} – {failed.label}: {failed.detail}.")
        else:
            no = True
            reasons.append(f"Tool-Calls: 0/{tools.total}{sim} – {failed.detail}." if failed
                           else f"Tool-Calls: 0/{tools.total}{sim}.")

    # 2) context
    if ctx.effective >= minimum:
        reasons.append(f"Kontext {_num(ctx.effective)} Token – reicht für OpenCode.")
    elif at_min is None:
        maybe = True
        reasons.append(f"Kontext nur {_num(ctx.effective)} Token, OpenCode braucht {_num(minimum)}.")
    else:
        min_ctx, min_v = at_min
        if min_ctx.effective < minimum:
            no = True
            reasons.append(f"Trainiert auf {_num(min_ctx.effective)} Token – OpenCode braucht {_num(minimum)}.")
        elif min_v.state in ("split", "cpu"):
            no = True
            reasons.append(f"Kontext nur {_num(ctx.effective)} Token; mit {_num(minimum)} käme es zum Teil-Offload "
                           f"(≈ {_gib(min_v.need_bytes)}).")
        else:
            maybe = True
            fit = "passt laut Schätzung" if min_v.state == "fits" else "knapp laut Schätzung"
            reasons.append(f"Kontext nur {_num(ctx.effective)} Token – eine Variante mit num_ctx {minimum} anlegen, "
                           f"{fit} (≈ {_gib(min_v.need_bytes)}).")

    # 3) the card
    if now.state in ("split", "cpu"):
        no = True
        reasons.append("Läuft beim wirksamen Kontext nicht komplett auf der GPU.")
    elif now.state == "tight":
        maybe = True
        reasons.append("VRAM knapp – andere Last auf der Karte kann es kippen.")

    state: FitState = "no" if no else "maybe" if maybe else "unknown" if untested else "fits"
    return OpencodeFit(
        state=state, reasons=[SUMMARY[state], *reasons],
        tools_passed=None if tools is None or tools.skipped else tools.passed,
        tools_total=None if tools is None else tools.total, tools_at=tools_at,
        tools_simulated=bool(tools and tools.simulated), context=ctx.effective, min_context=minimum,
        at_min=to_schema(at_min[1]) if at_min else None, eval_tps=eval_tps, config_key=config_key(name),
        block=opencode_block(name, ctx.effective, cfg))
