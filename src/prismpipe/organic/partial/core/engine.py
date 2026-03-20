"""Partial knowledge engine."""

from prismpipe.organic.partial.core.partial_result import PartialResult, ConfidenceLevel


class PartialKnowledgeEngine:
    """Engine for handling partial knowledge and results."""

    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence

    def process_partial(self, result: PartialResult) -> PartialResult:
        """Process a partial result."""
        if result.confidence >= self.min_confidence:
            result.is_complete = True
        return result

    def merge_partials(self, results: list[PartialResult]) -> PartialResult:
        """Merge multiple partial results."""
        if not results:
            return PartialResult(data={})

        merged_data = {}
        all_missing = []
        total_confidence = 0.0

        for r in results:
            merged_data.update(r.data)
            all_missing.extend(r.missing_keys)
            total_confidence += r.confidence

        avg_confidence = total_confidence / len(results)
        return PartialResult(
            data=merged_data,
            confidence=avg_confidence,
            missing_keys=list(set(all_missing))
        )