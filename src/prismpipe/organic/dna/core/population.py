"""Population for genetic algorithm."""

from prismpipe.organic.dna.core.genome import PipelineGenome


class Population:
    """Population of pipeline genomes."""

    def __init__(self, size: int = 100):
        self.size = size
        self.genomes: list[PipelineGenome] = []

    def add(self, genome: PipelineGenome) -> None:
        """Add a genome to the population."""
        self.genomes.append(genome)

    def get_best(self) -> PipelineGenome | None:
        """Get the genome with highest fitness."""
        if not self.genomes:
            return None
        return max(self.genomes, key=lambda g: g.fitness)

    def get_all(self) -> list[PipelineGenome]:
        """Get all genomes."""
        return self.genomes

    def clear(self) -> None:
        """Clear all genomes."""
        self.genomes.clear()

    def sort_by_fitness(self, reverse: bool = True) -> None:
        """Sort genomes by fitness."""
        self.genomes.sort(key=lambda g: g.fitness, reverse=reverse)