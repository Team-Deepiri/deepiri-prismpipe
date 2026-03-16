"""PrismPipe DNA - Pipeline genomes and evolution."""

import uuid
import random
from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict


@dataclass
class PipelineGenome:
    """A genome representing a successful pipeline configuration."""
    id: str
    path: list[str]
    success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    execution_count: int = 0
    fitness: float = 0.0
    variants: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenomeVariation:
    """A variation/mutation of a genome."""
    id: str
    parent_id: str
    path: list[str]
    created_at: float
    success: bool


class PipelineDNA:
    """
    Store and evolve successful pipeline genomes.
    
    Uses genetic programming to optimize pipeline paths.
    """

    def __init__(self):
        self._genomes: dict[str, PipelineGenome] = {}
        self._variations: dict[str, list[GenomeVariation]] = defaultdict(list)
        self._intent_index: dict[str, list[str]] = defaultdict(list)

    def record_successful_path(
        self,
        intent: str,
        path: list[str],
        success: bool,
        latency_ms: float
    ) -> str:
        """Record a successful execution path."""
        path_key = "->".join(path)
        
        # Check if genome exists
        existing_id = None
        for gid, g in self._genomes.items():
            if "->".join(g.path) == path_key:
                existing_id = gid
                break

        if existing_id:
            # Update existing
            genome = self._genomes[existing_id]
            genome.execution_count += 1
            
            # Update running averages
            n = genome.execution_count
            genome.avg_latency_ms = (genome.avg_latency_ms * (n - 1) + latency_ms) / n
            
            if not success:
                genome.success_rate = (genome.success_rate * (n - 1)) / n
            else:
                genome.success_rate = (genome.success_rate * (n - 1) + 1.0) / n
                
            return existing_id
        else:
            # Create new genome
            genome_id = str(uuid.uuid4())
            genome = PipelineGenome(
                id=genome_id,
                path=path,
                success_rate=1.0 if success else 0.0,
                avg_latency_ms=latency_ms,
                execution_count=1,
                fitness=self._calculate_fitness(1.0 if success else 0.0, latency_ms)
            )
            self._genomes[genome_id] = genome
            self._intent_index[intent].append(genome_id)
            
            return genome_id

    def get_best_path(self, intent: str) -> list[str] | None:
        """Get the best path for an intent."""
        genome_ids = self._intent_index.get(intent, [])
        
        if not genome_ids:
            return None
        
        # Find highest fitness
        best = None
        best_fitness = -1
        
        for gid in genome_ids:
            g = self._genomes.get(gid)
            if g and g.fitness > best_fitness:
                best = g
                best_fitness = g.fitness
        
        return best.path if best else None

    def mutate_path(self, path: list[str]) -> list[str]:
        """Create a mutation of a path."""
        if not path:
            return path
        
        mutation_type = random.choice(["insert", "remove", "replace", "swap"])
        
        if mutation_type == "insert" and len(path) < 10:
            # Insert a random capability
            insert_pos = random.randint(0, len(path))
            new_cap = f"capability_{random.randint(1, 100)}"
            return path[:insert_pos] + [new_cap] + path[insert_pos:]
        
        elif mutation_type == "remove" and len(path) > 2:
            # Remove a capability
            remove_pos = random.randint(0, len(path) - 1)
            return path[:remove_pos] + path[remove_pos_pos + 1:]
        
        elif mutation_type == "replace":
            # Replace a capability
            replace_pos = random.randint(0, len(path) - 1)
            new_cap = f"capability_{random.randint(1, 100)}"
            result = path.copy()
            result[replace_pos] = new_cap
            return result
        
        else:
            # Swap two capabilities
            if len(path) >= 2:
                i, j = random.sample(range(len(path)), 2)
                result = path.copy()
                result[i], result[j] = result[j], result[i]
                return result
        
        return path

    def crossover(self, path1: list[str], path2: list[str]) -> list[str]:
        """Create a child path from two parent paths."""
        if not path1 or not path2:
            return path1 or path2
        
        # Single-point crossover
        min_len = min(len(path1), len(path2))
        if min_len == 0:
            return path1 or path2
            
        point = random.randint(1, min_len - 1)
        
        child = path1[:point] + path2[point:]
        return child

    def create_variant(
        self,
        parent_id: str,
        path: list[str],
        success: bool
    ) -> str:
        """Create a variant/mutation of a genome."""
        var_id = str(uuid.uuid4())
        
        variation = GenomeVariation(
            id=var_id,
            parent_id=parent_id,
            path=path,
            created_at=0,  # Would use time.time()
            success=success
        )
        
        self._variations[parent_id].append(variation)
        
        return var_id

    def _calculate_fitness(self, success_rate: float, latency_ms: float) -> float:
        """Calculate fitness score for a genome."""
        # Higher success rate is better, lower latency is better
        latency_score = max(0, 1 - (latency_ms / 10000))  # 10s = 0 score
        return (success_rate * 0.7) + (latency_score * 0.3)

    def get_statistics(self) -> dict[str, Any]:
        """Get DNA statistics."""
        return {
            "total_genomes": len(self._genomes),
            "total_variations": sum(len(v) for v in self._variations.values()),
            "intents_tracked": len(self._intent_index),
            "avg_success_rate": sum(g.success_rate for g in self._genomes.values()) / max(1, len(self._genomes)),
            "avg_latency_ms": sum(g.avg_latency_ms for g in self._genomes.values()) / max(1, len(self._genomes))
        }


# Global instance
_default_dna: PipelineDNA | None = None


def get_pipeline_dna() -> PipelineDNA:
    """Get default pipeline DNA."""
    global _default_dna
    if _default_dna is None:
        _default_dna = PipelineDNA()
    return _default_dna


def set_pipeline_dna(dna: PipelineDNA) -> None:
    """Set default pipeline DNA."""
    global _default_dna
    _default_dna = dna
