"""Result combiner for merging partial results."""

from prismpipe.organic.partial.core.partial_result import PartialResult


class ResultCombiner:
    """Combines multiple partial results."""

    def combine(self, results: list[PartialResult], strategy: str = "average") -> PartialResult:
        """Combine multiple partial results."""
        if not results:
            return PartialResult(data={})
        
        if strategy == "average":
            return self._combine_average(results)
        elif strategy == "union":
            return self._combine_union(results)
        elif strategy == "highest":
            return self._combine_highest(results)
        return self._combine_average(results)

    def _combine_average(self, results: list[PartialResult]) -> PartialResult:
        merged_data = {}
        all_missing = set()
        total_confidence = 0.0

        for r in results:
            merged_data.update(r.data)
            all_missing.update(r.missing_keys)
            total_confidence += r.confidence

        return PartialResult(
            data=merged_data,
            confidence=total_confidence / len(results),
            missing_keys=list(all_missing)
        )

    def _combine_union(self, results: list[PartialResult]) -> PartialResult:
        merged_data = {}
        all_missing = set()
        max_confidence = 0.0

        for r in results:
            merged_data.update(r.data)
            all_missing.update(r.missing_keys)
            max_confidence = max(max_confidence, r.confidence)

        return PartialResult(data=merged_data, confidence=max_confidence, missing_keys=list(all_missing))

    def _combine_highest(self, results: list[PartialResult]) -> PartialResult:
        return max(results, key=lambda r: r.confidence)