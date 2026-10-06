"""Bootstrap settings: everything the process needs BEFORE the database exists.

Precedence (highest first): environment variables (ACC_*) > control-center.toml > defaults.
Runtime settings that users edit in the UI live in SQLite and belong to the settings module.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
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
