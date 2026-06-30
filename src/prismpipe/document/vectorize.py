"""document.vectorize capability contracts and runtime adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Mapping, Protocol, runtime_checkable

from prismpipe.core import Intent, Node, NodeResult, create_envelope
from prismpipe.engine import Organism, PrismEngine

DOCUMENT_VECTORIZE_CAPABILITY = "document.vectorize"
DOCUMENT_VECTORIZE_INPUT_KEY = "document_vectorize"  # envelope.input slot for the vectorize payload


class DocumentVectorizeValidationError(ValueError):
    """Raised when a document.vectorize payload does not match the contract."""


@dataclass
class DocumentChunk:
    """A single document chunk to vectorize."""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], index: int) -> "DocumentChunk":
        chunk_id = _optional_string(payload, "chunkId") or f"chunk_{index + 1}"
        text = _required_string(payload, "text", f"chunks[{index}].text")
        metadata = _optional_mapping(payload, "metadata", f"chunks[{index}].metadata")
        return cls(chunk_id=chunk_id, text=text, metadata=metadata)

    def to_payload(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "text": self.text,
            "metadata": dict(self.metadata),
        }


@dataclass
class VectorizeOptions:
    """Execution options for document.vectorize."""

    dimensions: int | None = None
    normalize: bool = False  # backend hint; runtime does not enforce until a vectorizer applies it
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "VectorizeOptions":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise DocumentVectorizeValidationError("options must be an object")

        dimensions = payload.get("dimensions")
        if dimensions is not None:
            if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
                raise DocumentVectorizeValidationError(
                    "options.dimensions must be a positive integer"
                )

        normalize = payload.get("normalize", False)
        if not isinstance(normalize, bool):
            raise DocumentVectorizeValidationError("options.normalize must be a boolean")

        metadata = _optional_mapping(payload, "metadata", "options.metadata")
        return cls(dimensions=dimensions, normalize=normalize, metadata=metadata)

    def to_payload(self) -> dict[str, Any]:
        return {
            "dimensions": self.dimensions,
            "normalize": self.normalize,
            "metadata": dict(self.metadata),
        }


@dataclass
class DocumentVectorizeInput:
    """Canonical document.vectorize input contract."""

    document_id: str
    chunks: list[DocumentChunk]
    metadata: dict[str, Any] = field(default_factory=dict)
    options: VectorizeOptions = field(default_factory=VectorizeOptions)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DocumentVectorizeInput":
        if not isinstance(payload, Mapping):
            raise DocumentVectorizeValidationError("document.vectorize payload must be an object")

        document_id = _required_string(payload, "documentId", "documentId")
        raw_chunks = payload.get("chunks")
        if not isinstance(raw_chunks, list) or not raw_chunks:
            raise DocumentVectorizeValidationError("chunks must be a non-empty list")

        chunks: list[DocumentChunk] = []
        for index, raw_chunk in enumerate(raw_chunks):
            if not isinstance(raw_chunk, Mapping):
                raise DocumentVectorizeValidationError(f"chunks[{index}] must be an object")
            chunks.append(DocumentChunk.from_payload(raw_chunk, index))

        metadata = _optional_mapping(payload, "metadata", "metadata")
        options = VectorizeOptions.from_payload(payload.get("options"))
        return cls(
            document_id=document_id,
            chunks=chunks,
            metadata=metadata,
            options=options,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "chunks": [chunk.to_payload() for chunk in self.chunks],
            "metadata": dict(self.metadata),
            "options": self.options.to_payload(),
        }


@dataclass
class VectorizedChunk:
    """A vectorized document chunk returned by a vectorizer backend."""

    chunk_id: str
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "text": self.text,
            "vector": list(self.vector),
            "metadata": dict(self.metadata),
        }


@dataclass
class VectorizeBackendResult:
    """Backend result returned by a Vectorizer implementation."""

    chunks: list[VectorizedChunk]
    dimensions: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Vectorizer(Protocol):
    """Boundary for real document vectorization providers."""

    provider: str
    model: str

    def vectorize(self, request: DocumentVectorizeInput) -> VectorizeBackendResult:
        """Vectorize the validated document request."""


@dataclass
class DocumentVectorizeOutput:
    """Structured document.vectorize success result."""

    document_id: str
    chunks: list[VectorizedChunk]
    dimensions: int
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_backend(
        cls,
        request: DocumentVectorizeInput,
        result: VectorizeBackendResult,
        provider: str,
        model: str,
    ) -> "DocumentVectorizeOutput":
        dimensions = _validate_backend_result(request, result)
        metadata = {
            "input": dict(request.metadata),
            "backend": dict(result.metadata),
            "options": request.options.to_payload(),
        }
        return cls(
            document_id=request.document_id,
            chunks=result.chunks,
            dimensions=dimensions,
            provider=provider,
            model=model,
            metadata=metadata,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "chunks": [chunk.to_payload() for chunk in self.chunks],
            "dimensions": self.dimensions,
            "provider": self.provider,
            "model": self.model,
            "metadata": dict(self.metadata),
        }


@dataclass
class DocumentVectorizeError:
    """Structured document.vectorize failure context."""

    code: str
    message: str
    document_id: str | None = None
    provider: str | None = None
    model: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "documentId": self.document_id,
            "provider": self.provider,
            "model": self.model,
            "details": dict(self.details),
        }


@dataclass
class DocumentVectorizeRuntimeResult:
    """Result of routing a document.vectorize payload through PrismEngine."""

    parent: Organism
    child: Organism
    success: bool
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @classmethod
    def from_child(cls, parent: Organism, child: Organism) -> "DocumentVectorizeRuntimeResult":
        output = child.state.get("document_vectorize")
        error = child.state.get("document_vectorize_error")
        return cls(
            parent=parent,
            child=child,
            success=not child.terminated and output is not None,
            output=output,
            error=error,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "parentId": self.parent.id,
            "childId": self.child.id,
            "output": self.output,
            "error": self.error,
        }


class DocumentVectorizeNode(Node):
    """PrismPipe node for the document.vectorize capability."""

    capability = DOCUMENT_VECTORIZE_CAPABILITY
    description = "Vectorize validated document chunks with an injected backend"

    def __init__(self, vectorizer: Vectorizer) -> None:
        super().__init__()
        self.vectorizer = vectorizer

    def process(self, envelope):
        payload = envelope.input.get(DOCUMENT_VECTORIZE_INPUT_KEY)
        provider = _vectorizer_provider(self.vectorizer)
        model = _vectorizer_model(self.vectorizer)

        try:
            request = DocumentVectorizeInput.from_payload(payload)
        except DocumentVectorizeValidationError as exc:
            error = DocumentVectorizeError(
                code="VALIDATION_ERROR",
                message=str(exc),
                provider=provider,
                model=model,
            )
            return _fail_envelope(envelope, error)

        try:
            backend_result = self.vectorizer.vectorize(request)
            output = DocumentVectorizeOutput.from_backend(
                request=request,
                result=backend_result,
                provider=provider,
                model=model,
            )
        except Exception as exc:
            error = DocumentVectorizeError(
                code="VECTORIZER_ERROR",
                message="Vectorizer backend failed",
                document_id=request.document_id,
                provider=provider,
                model=model,
                details={
                    "error": str(exc),
                    "errorType": type(exc).__name__,
                },
            )
            return _fail_envelope(envelope, error)

        envelope.state["document_vectorize"] = output.to_payload()
        envelope.state.pop("document_vectorize_error", None)
        envelope.set_next(None)
        return NodeResult(
            envelope=envelope,
            metadata={"action": "document.vectorize completed"},
        )


async def execute_document_vectorize(
    engine: PrismEngine,
    payload: Mapping[str, Any],
    vectorizer: Vectorizer,
    *,
    parent_state: Mapping[str, Any] | None = None,
    parent_metadata: Mapping[str, Any] | None = None,
    parent_input: Mapping[str, Any] | None = None,
    use_computation_sharing: bool = False,
) -> DocumentVectorizeRuntimeResult:
    """Route a canonical document.vectorize payload through a child organism."""

    engine.register_node(DocumentVectorizeNode(vectorizer))

    envelope_input: dict[str, Any] = dict(parent_input) if parent_input else {}
    envelope_input.update(
        {
            "event": DOCUMENT_VECTORIZE_CAPABILITY,
            DOCUMENT_VECTORIZE_INPUT_KEY: payload,
        }
    )

    metadata_kwargs = dict(parent_metadata or {})
    envelope = create_envelope(
        intent=Intent.CUSTOM,
        input_data=envelope_input,
        state=dict(parent_state or {}),
        source=metadata_kwargs.pop("source", DOCUMENT_VECTORIZE_CAPABILITY),
        **metadata_kwargs,
    )

    parent = engine.spawn_organism_from_envelope(envelope)
    child = await engine.execute_child_organism(
        parent,
        capability=DOCUMENT_VECTORIZE_CAPABILITY,
        patch_input={DOCUMENT_VECTORIZE_INPUT_KEY: payload},
        intent=Intent.CUSTOM,
        use_computation_sharing=use_computation_sharing,
    )
    return DocumentVectorizeRuntimeResult.from_child(parent, child)


def _fail_envelope(envelope, error: DocumentVectorizeError) -> NodeResult:
    envelope.state.pop("document_vectorize", None)
    envelope.state["document_vectorize_error"] = error.to_payload()
    envelope.set_next(None)
    envelope.terminate(error.message)
    return NodeResult(
        envelope=envelope,
        success=False,
        error=error.message,
        metadata={"action": f"document.vectorize failed: {error.code}"},
    )


def _validate_backend_result(
    request: DocumentVectorizeInput,
    result: VectorizeBackendResult,
) -> int:
    if not isinstance(result, VectorizeBackendResult):
        raise ValueError("Vectorizer returned an invalid result object")
    if len(result.chunks) != len(request.chunks):
        raise ValueError("Vectorizer returned a different number of chunks")

    dimensions = result.dimensions
    if dimensions is not None:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("Vectorizer returned invalid vector dimensions")

    for index, chunk in enumerate(result.chunks):
        if chunk.chunk_id != request.chunks[index].chunk_id:
            raise ValueError(f"Vectorizer returned mismatched chunk id for chunks[{index}]")
        if not isinstance(chunk.vector, list) or not chunk.vector:
            raise ValueError(f"Vectorizer returned an empty vector for chunks[{index}]")
        if not all(
            isinstance(value, Real) and not isinstance(value, bool)
            for value in chunk.vector
        ):
            raise ValueError(f"Vectorizer returned a non-numeric vector for chunks[{index}]")
        chunk_dimensions = len(chunk.vector)
        if dimensions is None:
            dimensions = chunk_dimensions
        elif dimensions != chunk_dimensions:
            raise ValueError("Vectorizer returned inconsistent vector dimensions")

    if dimensions is None or dimensions <= 0:
        raise ValueError("Vectorizer returned invalid vector dimensions")
    if request.options.dimensions is not None and request.options.dimensions != dimensions:
        raise ValueError("Vectorizer dimensions do not match requested options.dimensions")
    return dimensions


def _required_string(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DocumentVectorizeValidationError(f"{label} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DocumentVectorizeValidationError(f"{key} must be a non-empty string")
    return value


def _optional_mapping(payload: Mapping[str, Any], key: str, label: str) -> dict[str, Any]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DocumentVectorizeValidationError(f"{label} must be an object")
    return dict(value)


def _vectorizer_provider(vectorizer: Vectorizer) -> str:
    return str(getattr(vectorizer, "provider", "unknown"))


def _vectorizer_model(vectorizer: Vectorizer) -> str:
    return str(getattr(vectorizer, "model", "unknown"))
