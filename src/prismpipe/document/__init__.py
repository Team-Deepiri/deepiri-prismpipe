"""Document capability integrations."""

from prismpipe.document.vectorize import (
    DOCUMENT_VECTORIZE_CAPABILITY,
    DOCUMENT_VECTORIZE_INPUT_KEY,
    ChunkReference,
    DocumentReference,
    DocumentVectorizeError,
    DocumentVectorizeInput,
    DocumentVectorizeNode,
    DocumentVectorizeOutput,
    DocumentVectorizeRuntimeResult,
    StorageReference,
    VectorizeBackendResult,
    VectorizeOptions,
    VectorizedChunk,
    Vectorizer,
    execute_document_vectorize,
)

__all__ = [
    "DOCUMENT_VECTORIZE_CAPABILITY",
    "DOCUMENT_VECTORIZE_INPUT_KEY",
    "ChunkReference",
    "DocumentReference",
    "DocumentVectorizeError",
    "DocumentVectorizeInput",
    "DocumentVectorizeNode",
    "DocumentVectorizeOutput",
    "StorageReference",
    "DocumentVectorizeRuntimeResult",
    "VectorizeBackendResult",
    "VectorizeOptions",
    "VectorizedChunk",
    "Vectorizer",
    "execute_document_vectorize",
]
