"""Hash-based similarity for memory graph."""

import hashlib
from prismpipe.organic.memory_graph.core.node import RequestNode


class HashSimilarity:
    """Computes similarity between nodes using hash comparison."""

    def __init__(self):
        pass

    def compute_hash(self, node: RequestNode) -> str:
        """Compute a hash for a node."""
        data = f"{node.prompt}{node.intent_type}{'-'.join(node.path)}"
        return hashlib.sha256(data.encode()).hexdigest()

    def similarity(self, node1: RequestNode, node2: RequestNode) -> float:
        """Compute similarity between two nodes (0.0 to 1.0)."""
        hash1 = self.compute_hash(node1)
        hash2 = self.compute_hash(node2)
        
        if hash1 == hash2:
            return 1.0
        
        matching = sum(c1 == c2 for c1, c2 in zip(hash1, hash2))
        return matching / len(hash1)