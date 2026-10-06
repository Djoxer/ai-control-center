"""AI Control Center - control plane for the local AI box (Ollama, GPU, catalog, logs)."""
from importlib.metadata import PackageNotFoundError, version

try:
    # single source of truth: [project] version in pyproject.toml, read from the installed metadata
    __version__ = version("ai-control-center")
except PackageNotFoundError:
    # running from a plain source checkout without "uv sync" -> visible marker instead of a wrong number
    __version__ = "0.0.0+unknown"