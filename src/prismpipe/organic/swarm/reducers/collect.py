"""Collect reducer for aggregating swarm results."""

from prismpipe.revolutionary.swarm.core.result import SwarmResult


class CollectReducer:
    """Collects and aggregates results from swarm tasks."""

    def reduce(self, results: list[SwarmResult]) -> list:
        """Reduce results by collecting all data."""
        collected = []
        for result in results:
            if result.success and result.data is not None:
                collected.append(result.data)
        return collected

    def reduce_by_partition(self, results: list[SwarmResult]) -> dict[int, list]:
        """Reduce results grouped by partition."""
        partitioned = {}
        for result in results:
            if result.success and result.data is not None:
                if result.partition not in partitioned:
                    partitioned[result.partition] = []
                partitioned[result.partition].append(result.data)
        return partitioned