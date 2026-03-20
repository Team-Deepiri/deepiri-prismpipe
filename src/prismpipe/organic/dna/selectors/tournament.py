"""Tournament selector for genetic algorithm."""

import random
from prismpipe.revolutionary.dna.core.genome import PipelineGenome


class TournamentSelector:
    """Selects genomes using tournament selection."""

    def __init__(self, tournament_size: int = 3):
        self.tournament_size = tournament_size

    def select(self, population: list[PipelineGenome]) -> PipelineGenome:
        """Select a genome through tournament."""
        if not population:
            raise ValueError("Population is empty")
        
        tournament = random.sample(population, min(self.tournament_size, len(population)))
        return max(tournament, key=lambda g: g.fitness)

    def select_pair(self, population: list[PipelineGenome]) -> tuple[PipelineGenome, PipelineGenome]:
        """Select two genomes for crossover."""
        return self.select(population), self.select(population)