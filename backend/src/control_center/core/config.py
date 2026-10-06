"""Bootstrap settings: everything the process needs BEFORE the database exists.

Precedence (highest first): environment variables (ACC_*) > control-center.toml > defaults.
Runtime settings that users edit in the UI live in SQLite and belong to the settings module.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CONFIG_ENV = "ACC_CONFIG"                      # optional: absolute path to the toml file
DEFAULT_CONFIG = Path("control-center.toml")   # relative to the current working directory


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV, DEFAULT_CONFIG)).resolve()


class LogConfig(BaseModel):
    level: str = "INFO"
    file_name: str = "control-center.log"      # lives in <data_dir>/logs/
    max_bytes: int = 10 * 1024 * 1024          # rotate at 10 MB
    backup_count: int = 5                      # keep control-center.log.1 ... .5


class AdaptersConfig(BaseModel):
    """[adapters] section: where the external systems live and whether to simulate them.

    Core, not a module table: the dashboard reads Ollama and the GPU today, the catalog and a
    models module will need the very same connections later. One address, one place to change it.
    """
    ai_host: str = "127.0.0.1"                         # the AI box; on a second PC: its LAN IP
    ollama: Literal["http", "fake"] = "http"
    ollama_url: str = "http://{ai_host}:11434"          # {ai_host} is replaced, see expand()
    gpu: Literal["nvml", "fake", "none"] = "nvml"       # none = machine without NVIDIA GPU, no error shown
    gpu_index: int = Field(0, ge=0)                     # which GPU NVML reports (the AI box has exactly one)
    host: Literal["psutil", "fake"] = "psutil"          # CPU, RAM, disks, processes
    # built-in scenario folder in control_center/adapters/samples/ that the fake adapters replay
    fake_scenario: str = Field("normal", pattern=r"^[a-z0-9][a-z0-9-]*$")
    timeout_s: float = Field(2.0, gt=0, le=30)          # per call; a hanging source must not stall the others

    @field_validator("ai_host")
    @classmethod
    def _host_only(cls, value: str) -> str:
        """'http://192.168.5.54/' would become 'http://http://192.168.5.54/:11434' - stop it at boot."""
        value = value.strip()
        if not value or "://" in value or "/" in value or " " in value:
            raise ValueError(f"ai_host must be a host name or IP only, e.g. 192.168.5.54 "
                             f"(no http://, no /), got {value!r}")
        return value

    def expand(self, url: str) -> str:
        """'http://{ai_host}:3000' -> 'http://192.168.x.y:3000'. Lets the second PC change one value only."""
        return url.replace("{ai_host}", self.ai_host)


class ModulesConfig(BaseModel):
    """[modules] section. Besides 'disabled', every sub-table [modules.<key>] is passed to that module.

    The core does not know the module schemas; each module validates its own table on startup.
    """
    model_config = ConfigDict(extra="allow")           # keeps [modules.logs] etc. in model_extra

    disabled: list[str] = Field(default_factory=list)  # discovered modules are on unless listed here

    def section(self, key: str) -> dict:
        """Raw config table for one module, {} if the toml has none."""
        value = (self.model_extra or {}).get(key, {})
        return value if isinstance(value, dict) else {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACC_",
        env_nested_delimiter="__",             # ACC_LOG__LEVEL=DEBUG overrides [log] level
        extra="ignore",                        # unknown keys in the toml must not crash the boot
    )

    host: str = "127.0.0.1"                    # set "0.0.0.0" in the toml for LAN access
    port: int = 8090
    data_dir: Path = Path("data")              # SQLite, logs, snapshots
    frontend_dist: Path | None = None          # Angular build output; None = API only (dev mode)
    cors_origins: list[str] = Field(default_factory=list)  # only needed for "ng serve" without proxy
    log: LogConfig = Field(default_factory=LogConfig)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)
    modules: ModulesConfig = Field(default_factory=ModulesConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # order = precedence; a missing toml file is simply skipped by the source
        return init_settings, env_settings, TomlConfigSettingsSource(settings_cls, toml_file=config_path())

    @property
    def db_path(self) -> Path:
        return self.data_dir / "control-center.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def load_settings(**overrides) -> Settings:
    """Load settings and anchor relative paths at the config file's folder, not at the CWD.

    Autostart via Task Scheduler runs with a different working directory (often System32),
    so "data" relative to the CWD would silently end up somewhere else.
    """
    s = Settings(**overrides)
    base = config_path().parent
    if not s.data_dir.is_absolute():
        s.data_dir = (base / s.data_dir).resolve()
    if s.frontend_dist is not None and not s.frontend_dist.is_absolute():
        s.frontend_dist = (base / s.frontend_dist).resolve()
    return s
