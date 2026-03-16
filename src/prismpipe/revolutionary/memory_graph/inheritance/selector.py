"""Inheritance selector for memory graph."""

from prismpipe.revolutionary.memory_graph.core.node import RequestNode
from prismpipe.revolutionary.memory_graph.similarity.hash import HashSimilarity


class InheritanceSelector:
    """Selects nodes based on inheritance/similarity."""

    def __init__(self):
        self._similarity = HashSimilarity()

    def select(self, target: RequestNode, candidates: list[RequestNode], threshold: float = 0.7) -> list[RequestNode]:
        """Select candidates above similarity threshold."""
        selected = []
        for candidate in candidates:
            if candidate.id == target.id:
                continue
            sim = self._similarity.similarity(target, candidate)
            if sim >= threshold:
                selected.append(candidate)
        return selected

    def rank(self, target: RequestNode, candidates: list[RequestNode]) -> list[tuple[RequestNode, float]]:
        """Rank candidates by similarity."""
        results = []
        for candidate in candidates:
            if candidate.id == target.id:
                continue
            sim = self._similarity.similarity(target, candidate)
            results.append((candidate, sim))
        return sorted(results, key=lambda x: x[1], reverse=True)