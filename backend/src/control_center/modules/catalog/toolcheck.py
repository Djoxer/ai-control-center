"""Tool-call check: does a model answer with STRUCTURED tool calls, the way a coding agent (OpenCode) needs it?

Ollama's capability "tools" only says the chat template knows a tool format. Whether the model really uses it
is another question: qwen2.5-coder carries the label, but on the AI box it wrote its tool calls as plain text
(a JSON block in the answer) - OpenCode cannot execute that. So the test asks three small agent-like
requests, each with tool definitions like an agent sends them, and checks the answer:

1. read   - one tool, one string argument (the most common agent step: open a file)
2. choose - two tools, the request fits only one (the model must pick, not just call the first)
3. types  - a tool with string + integer arguments (agents validate argument types strictly)

Like a driving test with three manoeuvres: one wrong is a "maybe", none right is a "no".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_center.adapters.ollama import ChatResult, ToolCall


def _tool(name: str, description: str, properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(properties)}}}


READ_FILE = _tool("read_file", "Read a file from the project and return its content.",
                  {"path": {"type": "string", "description": "path of the file, relative to the project root"}})
RUN_COMMAND = _tool("run_command", "Run a shell command in the project folder and return its output.",
                    {"command": {"type": "string", "description": "the command line to run"}})
SEARCH = _tool("search", "Search the project for a text pattern.",
               {"pattern": {"type": "string", "description": "text or regular expression to look for"},
                "path": {"type": "string", "description": "folder to search in, relative to the project root"},
                "max_results": {"type": "integer", "description": "maximum number of matches"}})

SYSTEM = ("Du bist ein Coding-Agent in einem Softwareprojekt. Nutze die bereitgestellten Werkzeuge, "
          "statt Ergebnisse zu raten oder Befehle nur zu beschreiben.")


def _norm_path(value: Any) -> str:
    text = str(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


@dataclass(frozen=True)
class Case:
    key: str
    label: str                                   # German, shown in the result
    request: str                                 # the user message
    tools: tuple[dict[str, Any], ...]
    expect: str                                  # tool name
    check: Callable[[dict[str, Any]], str | None]   # arguments -> German problem, or None when fine


def _check_read(args: dict[str, Any]) -> str | None:
    if "path" not in args:
        return "Argument path fehlt"
    return None if _norm_path(args["path"]).endswith("src/app/app.config.ts") else f"path = {args['path']!r}"


def _check_run(args: dict[str, Any]) -> str | None:
    if "command" not in args:
        return "Argument command fehlt"
    return None if "npm test" in str(args["command"]) else f"command = {args['command']!r}"


def _check_search(args: dict[str, Any]) -> str | None:
    problems = []
    if "TODO" not in str(args.get("pattern", "")):
        problems.append(f"pattern = {args.get('pattern')!r}")
    if _norm_path(args.get("path", "")) != "src":
        problems.append(f"path = {args.get('path')!r}")
    limit = args.get("max_results")
    if isinstance(limit, bool) or not isinstance(limit, int):
        problems.append(f"max_results = {limit!r}" + (" (Text statt Zahl)" if isinstance(limit, str) else ""))
    elif limit != 5:
        problems.append(f"max_results = {limit}")
    return ", ".join(problems) or None


CASES: tuple[Case, ...] = (
    Case("read", "Datei lesen", "Zeig mir den Inhalt der Datei „src/app/app.config.ts“.",
         (READ_FILE,), "read_file", _check_read),
    Case("choose", "Werkzeug wählen", "Starte die Unit-Tests mit dem Befehl „npm test“.",
         (READ_FILE, RUN_COMMAND), "run_command", _check_run),
    Case("types", "Argumente mit Typen", "Suche nach „TODO“ im Ordner „src“, höchstens 5 Treffer.",
         (SEARCH,), "search", _check_search),
)


def messages(case: Case) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case.request}]


def _call_text(call: ToolCall) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
    return f"{call.name}({args})"


def evaluate(case: Case, answer: ChatResult) -> tuple[bool, str]:
    """(passed, German detail). Order of the checks = what an agent would stumble over first."""
    if not answer.tool_calls:
        text = answer.content.strip()
        if answer.done_reason == "length":
            return False, (f"Abgebrochen nach {answer.eval_tokens or '?'} Token ohne Tool-Call"
                           + (" (denkt zu lange)" if answer.thinking_chars else ""))
        if case.expect in text or '"name"' in text or "```" in text:
            return False, "Tool-Call nur als Text – ein Agent kann ihn nicht ausführen"
        return False, "Kein Tool-Call, nur eine Antwort in Worten"
    call = answer.tool_calls[0]
    if call.name != case.expect:
        return False, f"Falsches Werkzeug: {call.name} statt {case.expect}"
    if "_raw" in call.arguments:
        return False, "Argumente sind kein gültiges JSON"
    problem = case.check(call.arguments)
    if problem:
        return False, f"Argumente falsch: {problem}"
    return True, _call_text(call)


NO_TOOLS = "does not support tools"         # Ollama's refusal text for templates without a tool format
