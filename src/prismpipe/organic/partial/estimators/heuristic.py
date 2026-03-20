"""Heuristic confidence estimator."""

from prismpipe.organic.partial.core.partial_result import PartialResult, ConfidenceLevel


class HeuristicConfidenceEstimator:
    """Estimates confidence using heuristic rules."""

    def __init__(self, completeness_weight: float = 0.5, data_quality_weight: float = 0.5):
        self.completeness_weight = completeness_weight
        self.data_quality_weight = data_quality_weight

    def estimate(self, result: PartialResult) -> float:
        """Estimate confidence for a partial result."""
        completeness = self._calculate_completeness(result)
        data_quality = self._estimate_data_quality(result.data)
        return (completeness * self.completeness_weight) + (data_quality * self.data_quality_weight)

    def _calculate_completeness(self, result: PartialResult) -> float:
        total_keys = len(result.data) + len(result.missing_keys)
        if total_keys == 0:
            return 0.0
        return len(result.data) / total_keys

    def _estimate_data_quality(self, data: dict) -> float:
        if not data:
            return 0.0
        non_empty = sum(1 for v in data.values() if v is not None and v != "")
        return non_empty / len(data) if data else 0.0