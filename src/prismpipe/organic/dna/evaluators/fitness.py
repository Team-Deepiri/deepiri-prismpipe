"""Fitness evaluator for genetic algorithm."""

from prismpipe.organic.dna.core.genome import PipelineGenome


class FitnessEvaluator:
    """Evaluates fitness of pipeline genomes."""

    def __init__(self):
        pass

    def evaluate(self, genome: PipelineGenome, test_cases: list = None) -> float:
        """Evaluate fitness of a genome."""
        if not genome.genes:
            return 0.0
        
        fitness = 0.0
        
        if self._is_valid_pipeline(genome.genes):
            fitness += 0.5
        
        fitness += self._evaluate_diversity(genome.genes)
        
        if test_cases:
            fitness += self._evaluate_on_tests(genome.genes, test_cases)
        
        genome.fitness = fitness
        return fitness

    def _is_valid_pipeline(self, genes: list[str]) -> bool:
        valid_genes = {"fetch", "transform", "filter", "aggregate", "validate"}
        return all(g in valid_genes for g in genes)

    def _evaluate_diversity(self, genes: list[str]) -> float:
        if not genes:
            return 0.0
        unique = len(set(genes))
        return unique / len(genes)

    def _evaluate_on_tests(self, genes: list[str], test_cases: list) -> float:
        return 0.0

    def evaluate_population(self, population: list[PipelineGenome], test_cases: list = None) -> list[PipelineGenome]:
        """Evaluate all genomes in a population."""
        for genome in population:
            self.evaluate(genome, test_cases)
        return population