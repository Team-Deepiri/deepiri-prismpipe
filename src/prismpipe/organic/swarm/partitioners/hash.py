"""Hash partitioner for distributing tasks."""

import hashlib


class HashPartitioner:
    """Partitions tasks based on hash of payload."""

    def partition(self, payload: any, index: int, num_partitions: int) -> int:
        """Determine partition for a payload."""
        data = str(payload) + str(index)
        hash_val = int(hashlib.md5(data.encode()).hexdigest(), 16)
        return hash_val % num_partitions