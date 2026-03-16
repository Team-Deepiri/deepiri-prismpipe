"""PrismPipe multi-tenancy."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Tenant:
    """Tenant configuration."""
    id: str
    name: str
    quota: dict[str, float] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QuotaUsage:
    """Track quota usage per tenant."""
    requests: int = 0
    compute_time_ms: float = 0.0
    storage_bytes: int = 0


class TenantManager:
    """Manage tenants and quotas."""

    def __init__(self):
        self._tenants: dict[str, Tenant] = {}
        self._usage: dict[str, QuotaUsage] = {}
        self._capability_permissions: dict[str, list[str]] = {}

    def add_tenant(self, tenant: Tenant) -> None:
        """Add a tenant."""
        self._tenants[tenant.id] = tenant
        self._usage[tenant.id] = QuotaUsage()

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        """Get tenant by ID."""
        return self._tenants.get(tenant_id)

    def has_capability_access(self, tenant_id: str, capability: str) -> bool:
        """Check if tenant has access to a capability."""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return False
        if not tenant.capabilities:
            return True
        return capability in tenant.capabilities

    def check_quota(self, tenant_id: str, resource: str) -> bool:
        """Check if tenant has quota for a resource."""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return True
        usage = self._usage.get(tenant_id, QuotaUsage())
        limit = tenant.quota.get(resource, float("inf"))
        if resource == "requests":
            return usage.requests < limit
        elif resource == "compute_time_ms":
            return usage.compute_time_ms < limit
        return True

    def record_usage(self, tenant_id: str, resource: str, value: float) -> None:
        """Record resource usage."""
        if tenant_id not in self._usage:
            self._usage[tenant_id] = QuotaUsage()
        usage = self._usage[tenant_id]
        if resource == "requests":
            usage.requests += int(value)
        elif resource == "compute_time_ms":
            usage.compute_time_ms += value

    def get_usage(self, tenant_id: str) -> QuotaUsage:
        """Get quota usage for tenant."""
        return self._usage.get(tenant_id, QuotaUsage())

    def reset_usage(self, tenant_id: str) -> None:
        """Reset quota usage for tenant."""
        if tenant_id in self._usage:
            self._usage[tenant_id] = QuotaUsage()


# Context variable for current tenant
current_tenant_var: ContextVar[str | None] = ContextVar("current_tenant", default=None)


class TenantContext:
    """Context manager for tenant operations."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def __enter__(self):
        current_tenant_var.set(self.tenant_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        current_tenant_var.set(None)


def get_current_tenant() -> str | None:
    """Get current tenant ID from context."""
    return current_tenant_var.get()


# Default tenant manager
_default_manager: TenantManager | None = None


def get_tenant_manager() -> TenantManager:
    """Get default tenant manager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = TenantManager()
    return _default_manager


def set_tenant_manager(manager: TenantManager) -> None:
    """Set default tenant manager."""
    global _default_manager
    _default_manager = manager
