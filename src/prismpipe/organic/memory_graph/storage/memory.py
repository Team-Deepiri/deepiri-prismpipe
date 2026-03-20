"""In-memory storage for memory graph."""

from prismpipe.revolutionary.memory_graph.core.node import RequestNode


class InMemoryStorage:
    """In-memory storage implementation for memory graph."""

    def __init__(self):
        self._storage: dict[str, RequestNode] = {}

    def store(self, node: RequestNode) -> None:
        """Store a node."""
        self._storage[node.id] = node

    def retrieve(self, node_id: str) -> RequestNode | None:
        """Retrieve a node by ID."""
        return self._storage.get(node_id)

    def delete(self, node_id: str) -> bool:
        """Delete a node by ID."""
        if node_id in self._storage:
            del self._storage[node_id]
            return True
        return False

    def list_all(self) -> list[RequestNode]:
        """List all stored nodes."""
        return list(self._storage.values())

    def clear(self) -> None:
        """Clear all storage."""
        self._storage.clear()