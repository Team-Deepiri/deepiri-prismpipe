"""PrismPipe feature flags."""

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FeatureFlag:
    """Feature flag definition."""
    name: str
    enabled: bool = False
    variants: dict[str, float] = field(default_factory=dict)
    rollout_percent: float = 0.0


class FeatureFlags:
    """Feature flags management."""

    def __init__(self, config: dict[str, Any] | None = None):
        self._flags: dict[str, FeatureFlag] = {}
        self._tenant_overrides: dict[str, dict[str, bool]] = {}
        
        if config:
            for name, value in config.items():
                if isinstance(value, dict):
                    self._flags[name] = FeatureFlag(
                        name=name,
                        enabled=value.get("enabled", False),
                        variants=value.get("variants", {}),
                        rollout_percent=value.get("rollout_percent", 0.0),
                    )
                else:
                    self._flags[name] = FeatureFlag(name=name, enabled=bool(value))

    def is_enabled(self, flag: str, tenant: str | None = None) -> bool:
        """Check if a flag is enabled."""
        if tenant and tenant in self._tenant_overrides:
            return self._tenant_overrides[tenant].get(flag, False)

        flag_obj = self._flags.get(flag)
        if not flag_obj:
            return False

        if flag_obj.rollout_percent > 0:
            return random.random() * 100 < flag_obj.rollout_percent

        return flag_obj.enabled

    def get_variant(self, flag: str, default: str = "control", tenant: str | None = None) -> str:
        """Get A/B test variant for a flag."""
        flag_obj = self._flags.get(flag)
        if not flag_obj or not flag_obj.variants:
            return default

        if tenant:
            hash_input = f"{flag}:{tenant}"
        else:
            hash_input = f"{flag}:{random.random()}"

        bucket = int(hashlib.md5(hash_input.encode()).hexdigest(), 16) % 100
        cumulative = 0

        for variant, percentage in flag_obj.variants.items():
            cumulative += percentage * 100
            if bucket < cumulative:
                return variant

        return default

    def set_enabled(self, flag: str, enabled: bool, tenant: str | None = None) -> None:
        """Enable or disable a flag."""
        if tenant:
            if tenant not in self._tenant_overrides:
                self._tenant_overrides[tenant] = {}
            self._tenant_overrides[tenant][flag] = enabled
        else:
            if flag not in self._flags:
                self._flags[flag] = FeatureFlag(name=flag)
            self._flags[flag].enabled = enabled

    def add_flag(self, flag: FeatureFlag) -> None:
        """Add a new flag."""
        self._flags[flag.name] = flag

    def get_all_flags(self) -> dict[str, FeatureFlag]:
        """Get all flags."""
        return self._flags.copy()

    def list_enabled(self, tenant: str | None = None) -> list[str]:
        """List enabled flags."""
        enabled = []
        for name, flag in self._flags.items():
            if self.is_enabled(name, tenant):
                enabled.append(name)
        return enabled


# Default instance
_default_flags: FeatureFlags | None = None


def get_feature_flags() -> FeatureFlags:
    """Get default feature flags."""
    global _default_flags
    if _default_flags is None:
        _default_flags = FeatureFlags()
    return _default_flags


def set_feature_flags(flags: FeatureFlags) -> None:
    """Set default feature flags."""
    global _default_flags
    _default_flags = flags
