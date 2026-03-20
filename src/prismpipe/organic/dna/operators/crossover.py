"""Crossover operator for genetic algorithm."""

import random
from prismpipe.organic.dna.core.genome import PipelineGenome


class CrossoverOperator:
    """Combines two genomes to create offspring."""

    def __init__(self, crossover_rate: float = 0.7):
        self.crossover_rate = crossover_rate

    def crossover(self, parent1: PipelineGenome, parent2: PipelineGenome) -> tuple[PipelineGenome, PipelineGenome]:
        """Perform crossover between two parents."""
        if random.random() > self.crossover_rate:
            return parent1.copy(), parent2.copy()

        min_len = min(len(parent1.genes), len(parent2.genes))
        if min_len == 0:
            return parent1.copy(), parent2.copy()

        point = random.randint(1, min_len - 1)

        child1_genes = parent1.genes[:point] + parent2.genes[point:]
        child2_genes = parent2.genes[:point] + parent1.genes[point:]

        child1 = PipelineGenome(genes=child1_genes)
        child2 = PipelineGenome(genes=child2_genes)

        return child1, child2

    def uniform_crossover(self, parent1: PipelineGenome, parent2: PipelineGenome) -> tuple[PipelineGenome, PipelineGenome]:
        """Perform uniform crossover."""
        max_len = max(len(parent1.genes), len(parent2.genes))
        min_len = min(len(parent1.genes), len(parent2.genes))

        child1_genes = []
        child2_genes = []

        for i in range(max_len):
            if i >= min_len:
                gene1 = parent1.genes[i] if i < len(parent1.genes) else None
                gene2 = parent2.genes[i] if i < len(parent2.genes) else None
                child1_genes.append(gene1 or gene2)
                child2_genes.append(gene2 or gene1)
            else:
                if random.random() < 0.5:
                    child1_genes.append(parent1.genes[i])
                    child2_genes.append(parent2.genes[i])
                else:
                    child1_genes.append(parent2.genes[i])
                    child2_genes.append(parent1.genes[i])

        return PipelineGenome(genes=child1_genes), PipelineGenome(genes=child2_genes)