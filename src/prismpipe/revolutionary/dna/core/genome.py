"""Pipeline genome for genetic algorithm."""

from dataclasses import dataclass, field
from typing import Any
import uuid


@dataclass
class PipelineGenome:
    """Genome representing a pipeline configuration."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    genes: list[str] = field(default_factory=list)
    fitness: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "genes": self.genes,
            "fitness": self.fitness,
            "metadata": self.metadata,
        }

    def copy(self) -> "PipelineGenome":
        """Create a copy of this genome."""
        return PipelineGenome(
            genes=self.genes.copy(),
            fitness=self.fitness,
            metadata=self.metadata.copy(),
        )