"""Memory graph for storing request relationships."""

from prismpipe.revolutionary.memory_graph.core.node import RequestNode


class MemoryGraph:
    """Graph structure for storing request relationships."""

    def __init__(self):
        self._nodes: dict[str, RequestNode] = {}
        self._edges: dict[str, list[str]] = {}

    def add_node(self, node: RequestNode) -> None:
        """Add a node to the graph."""
        self._nodes[node.id] = node
        if node.id not in self._edges:
            self._edges[node.id] = []

    def add_edge(self, from_id: str, to_id: str) -> None:
        """Add an edge between two nodes."""
        if from_id in self._edges:
            self._edges[from_id].append(to_id)
        else:
            self._edges[from_id] = [to_id]

    def get_node(self, node_id: str) -> RequestNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_neighbors(self, node_id: str) -> list[RequestNode]:
        """Get neighbors of a node."""
        neighbor_ids = self._edges.get(node_id, [])
        return [self._nodes[nid] for nid in neighbor_ids if nid in self._nodes]

    def get_all_nodes(self) -> list[RequestNode]:
        """Get all nodes in the graph."""
        return list(self._nodes.values())

    def clear(self) -> None:
        """Clear the graph."""
        self._nodes.clear()
        self._edges.clear()