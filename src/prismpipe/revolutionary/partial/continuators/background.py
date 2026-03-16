"""Background continuator for continuing partial tasks."""

from prismpipe.revolutionary.partial.core.task import BackgroundTask


class BackgroundContinuator:
    """Continues computation in the background."""

    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}

    def submit(self, task: BackgroundTask) -> str:
        """Submit a task for background execution."""
        self._tasks[task.id] = task
        return task.id

    def get(self, task_id: str) -> BackgroundTask | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def list_pending(self) -> list[BackgroundTask]:
        """List all pending tasks."""
        return [t for t in self._tasks.values() if t.status == "pending"]

    def remove(self, task_id: str) -> bool:
        """Remove a completed task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False