"""Module discovery: scan the modules package, import each sub-package, pick up its MODULE."""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass

from control_center.core.module import ModuleSpec

log = logging.getLogger("control_center.core.loader")


@dataclass
class Discovered:
    key: str                        # folder name
    spec: ModuleSpec | None         # None = not loadable (see error) or not ready yet
    error: str | None = None


def discover(package: str = "control_center.modules") -> list[Discovered]:
    pkg = importlib.import_module(package)
    found: list[Discovered] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue                                    # plain files and _private folders are not modules
        try:
            mod = importlib.import_module(f"{package}.{info.name}")
        except Exception as exc:
            # a syntax error in one module must not keep the other modules from booting
            log.exception("module %s failed to import", info.name)
            found.append(Discovered(info.name, None, f"import failed: {exc!r}"))
            continue
        spec = getattr(mod, "MODULE", None)
        if spec is None:
            log.info("module folder %s has no MODULE yet - skipped", info.name)
            continue                                    # scaffold folder, not an error
        if not isinstance(spec, ModuleSpec):
            found.append(Discovered(info.name, None, "MODULE is not a ModuleSpec"))
            continue
        if spec.key != info.name:
            # key drives URL, settings namespace and logger name - drift between them causes ghost bugs
            found.append(Discovered(info.name, None, f"MODULE.key '{spec.key}' != folder '{info.name}'"))
            continue
        found.append(Discovered(info.name, spec))
    # deterministic startup and menu order; key as tie-breaker
    found.sort(key=lambda d: (d.spec.order if d.spec else 9999, d.key))
    return found
