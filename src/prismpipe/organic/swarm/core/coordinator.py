"""Swarm coordinator for managing distributed tasks."""

from prismpipe.organic.swarm.core.swarm import SwarmEnvelope
from prismpipe.organic.swarm.core.result import SwarmResult


class SwarmCoordinator:
    """Coordinates swarm tasks across partitions."""

    def __init__(self, num_partitions: int = 4):
        self.num_partitions = num_partitions
        self._results: dict[str, SwarmResult] = {}

    def create_envelopes(self, payloads: list, partitioner) -> list[SwarmEnvelope]:
        """Create envelopes from payloads using a partitioner."""
        envelopes = []
        for i, payload in enumerate(payloads):
            partition = partitioner.partition(payload, i, self.num_partitions)
            envelope = SwarmEnvelope(payload=payload, partition=partition)
            envelopes.append(envelope)
        return envelopes

    def add_result(self, result: SwarmResult) -> None:
        """Add a result from a task."""
        self._results[result.task_id] = result

    def get_result(self, task_id: str) -> SwarmResult | None:
        """Get a result by task ID."""
        return self._results.get(task_id)

    def get_all_results(self) -> list[SwarmResult]:
        """Get all results."""
        return list(self._results.values())

    def get_successful_results(self) -> list[SwarmResult]:
        """Get only successful results."""
        return [r for r in self._results.values() if r.success]

    def clear(self) -> None:
        """Clear all results."""
        self._results.clear()