"""Mutation operator for genetic algorithm."""

import random
from prismpipe.revolutionary.dna.core.genome import PipelineGenome


class MutationOperator:
    """Mutates genome genes."""

    def __init__(self, mutation_rate: float = 0.1, gene_pool: list[str] = None):
        self.mutation_rate = mutation_rate
        self.gene_pool = gene_pool or ["fetch", "transform", "filter", "aggregate", "validate"]

    def mutate(self, genome: PipelineGenome) -> PipelineGenome:
        """Apply mutation to a genome."""
        mutated = genome.copy()
        for i in range(len(mutated.genes)):
            if random.random() < self.mutation_rate:
                mutated.genes[i] = random.choice(self.gene_pool)
        return mutated

    def mutate_insert(self, genome: PipelineGenome) -> PipelineGenome:
        """Insert a random gene."""
        mutated = genome.copy()
        pos = random.randint(0, len(mutated.genes))
        mutated.genes.insert(pos, random.choice(self.gene_pool))
        return mutated

    def mutate_delete(self, genome: PipelineGenome) -> PipelineGenome:
        """Delete a random gene."""
        if len(genome.genes) <= 1:
            return genome.copy()
        mutated = genome.copy()
        pos = random.randint(0, len(mutated.genes) - 1)
        del mutated.genes[pos]
        return mutated