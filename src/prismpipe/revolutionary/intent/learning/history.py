"""History-based learning for intent paths."""

from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict


@dataclass
class ExecutionRecord:
    """Record of path execution."""
    intent: str
    path: list[str]
    success: bool
    latency_ms: float
    timestamp: float


class HistoryLearner:
    """Learn from execution history to improve path planning."""
    
    def __init__(self):
        self._history: list[ExecutionRecord] = []
        self._path_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "successes": 0,
            "failures": 0,
            "latencies": [],
        })
    
    def record(self, intent: str, path: list[str], success: bool, latency_ms: float) -> None:
        """Record an execution."""
        record = ExecutionRecord(
            intent=intent,
            path=path,
            success=success,
            latency_ms=latency_ms,
            timestamp=0,  # Would use time.time()
        )
        self._history.append(record)
        
        path_key = "->".join(path)
        stats = self._path_stats[path_key]
        if success:
            stats["successes"] += 1
        else:
            stats["failures"] += 1
        stats["latencies"].append(latency_ms)
    
    def get_best_path(self, intent: str) -> list[str] | None:
        """Get best performing path for intent."""
        # Filter history by intent
        intent_history = [r for r in self._history if r.intent == intent]
        if not intent_history:
            return None
        
        # Find path with highest success rate and lowest latency
        path_performance: dict[str, tuple[float, float]] = {}
        
        for record in intent_history:
            path_key = "->".join(record.path)
            if path_key not in path_performance:
                successes = sum(1 for r in intent_history if "->".join(r.path) == path_key and r.success)
                total = sum(1 for r in intent_history if "->".join(r.path) == path_key)
                latencies = [r.latency_ms for r in intent_history if "->".join(r.path) == path_key]
                avg_latency = sum(latencies) / len(latencies) if latencies else float('inf')
                success_rate = successes / total if total > 0 else 0
                path_performance[path_key] = (success_rate, avg_latency)
        
        if not path_performance:
            return None
        
        # Sort by success rate (desc), then latency (asc)
        best = min(path_performance.items(), key=lambda x: (-x[1][0], x[1][1]))
        return best[0].split("->")
    
    def get_stats(self) -> dict[str, Any]:
        """Get learning statistics."""
        return {
            "total_records": len(self._history),
            "unique_paths": len(self._path_stats),
            "path_stats": dict(self._path_stats),
        }
