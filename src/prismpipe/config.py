"""PrismPipe configuration management."""

import os
import re
from pathlib import Path
from typing import Any

import yaml


class Config:
    """PrismPipe configuration."""

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._env_vars = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value with dot notation support."""
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        if value is None:
            return default
        return self._substitute_env_vars(value)

    def set(self, key: str, value: Any) -> None:
        """Set config value with dot notation support."""
        keys = key.split(".")
        config = self._config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value

    def _substitute_env_vars(self, value: Any) -> Any:
        """Substitute ${VAR} with environment variables."""
        if isinstance(value, str):
            pattern = r'\$\{([^}]+)\}'
            def replacer(match: re.Match) -> str:
                var_name = match.group(1)
                env_val = os.environ.get(var_name)
                return env_val if env_val is not None else match.group(0)
            return re.sub(pattern, replacer, value)
        elif isinstance(value, dict):
            return {k: self._substitute_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._substitute_env_vars(v) for v in value]
        return value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from YAML/JSON file or defaults."""
    if path is None:
        path = os.environ.get("PRISMPIPE_CONFIG")

    if path and os.path.exists(str(path)):
        path_str = str(path)
        with open(path_str) as f:
            if path_str.endswith(".json"):
                import json
                data = json.load(f)
            else:
                data = yaml.safe_load(f) or {}
        return Config(data)

    return Config({
        "pipeline": {
            "max_iterations": 100,
            "default_timeout": 30,
        },
        "logging": {
            "level": "INFO",
            "format": "json",
        },
    })


# Default config instance
_default_config: Config | None = None


def get_config() -> Config:
    """Get default configuration."""
    global _default_config
    if _default_config is None:
        _default_config = load_config()
    return _default_config


def set_config(config: Config) -> None:
    """Set default configuration."""
    global _default_config
    _default_config = config
