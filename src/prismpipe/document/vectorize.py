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
class StorageReference:
    """LIS storage reference for document and chunk payloads."""

    provider: str | None = None
    bucket: str | None = None
    key: str | None = None
    uri: str | None = None
    version_id: str | None = None
    content_type: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        label: str = "storage",
    ) -> "StorageReference":
        if not isinstance(payload, Mapping):
            raise DocumentVectorizeValidationError(f"{label} must be an object")

        size_bytes = _optional_int(payload, "sizeBytes", f"{label}.sizeBytes")
        if size_bytes is not None and size_bytes < 0:
            raise DocumentVectorizeValidationError(
                f"{label}.sizeBytes must be a non-negative integer"
            )

        return cls(
            provider=_optional_string(payload, "provider", f"{label}.provider"),
            bucket=_optional_string(payload, "bucket", f"{label}.bucket"),
            key=_optional_string(payload, "key", f"{label}.key"),
            uri=_optional_string(payload, "uri", f"{label}.uri"),
            version_id=_optional_string(payload, "versionId", f"{label}.versionId"),
            content_type=_optional_string(payload, "contentType", f"{label}.contentType"),
            checksum=_optional_string(payload, "checksum", f"{label}.checksum"),
            size_bytes=size_bytes,
            metadata=_optional_mapping(payload, "metadata", f"{label}.metadata"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "key": self.key,
            "uri": self.uri,
            "versionId": self.version_id,
            "contentType": self.content_type,
            "checksum": self.checksum,
            "sizeBytes": self.size_bytes,
            "metadata": dict(self.metadata),
        }


@dataclass
class DocumentReference:
    """LIS document reference embedded in a vectorize route payload."""

    document_id: str
    title: str | None = None
    source_type: str | None = None
    mime_type: str | None = None
    fingerprint: str | None = None
    storage: StorageReference | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DocumentReference":
        if not isinstance(payload, Mapping):
            raise DocumentVectorizeValidationError("document must be an object")

        raw_storage = payload.get("storage")
        storage = None
        if raw_storage is not None:
            if not isinstance(raw_storage, Mapping):
                raise DocumentVectorizeValidationError("document.storage must be an object")
            storage = StorageReference.from_payload(raw_storage, "document.storage")

        return cls(
            document_id=_required_string(payload, "documentId", "document.documentId"),
            title=_optional_string(payload, "title", "document.title"),
            source_type=_optional_string(payload, "sourceType", "document.sourceType"),
            mime_type=_optional_string(payload, "mimeType", "document.mimeType"),
            fingerprint=_optional_string(payload, "fingerprint", "document.fingerprint"),
            storage=storage,
            metadata=_optional_mapping(payload, "metadata", "document.metadata"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "title": self.title,
            "sourceType": self.source_type,
            "mimeType": self.mime_type,
            "fingerprint": self.fingerprint,
            "storage": self.storage.to_payload() if self.storage else None,
            "metadata": dict(self.metadata),
        }


@dataclass
class ChunkReference:
    """LIS chunk reference for document.vectorize."""

    chunk_id: str
    index: int
    text: str | None
    document_id: str | None = None
    token_count: int | None = None
    storage: StorageReference | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], index: int) -> "ChunkReference":
        chunk_label = f"chunks[{index}]"
        if not isinstance(payload, Mapping):
            raise DocumentVectorizeValidationError(f"{chunk_label} must be an object")

        chunk_index = _required_int(payload, "index", f"{chunk_label}.index")
        if chunk_index < 0:
            raise DocumentVectorizeValidationError(
                f"{chunk_label}.index must be a non-negative integer"
            )

        token_count = _optional_int(payload, "tokenCount", f"{chunk_label}.tokenCount")
        if token_count is not None and token_count < 0:
            raise DocumentVectorizeValidationError(
                f"{chunk_label}.tokenCount must be a non-negative integer"
            )

        raw_storage = payload.get("storage")
        storage = None
        if raw_storage is not None:
            if not isinstance(raw_storage, Mapping):
                raise DocumentVectorizeValidationError(f"{chunk_label}.storage must be an object")
            storage = StorageReference.from_payload(raw_storage, f"{chunk_label}.storage")

        return cls(
            chunk_id=_required_string(payload, "chunkId", f"{chunk_label}.chunkId"),
            document_id=_optional_string(payload, "documentId", f"{chunk_label}.documentId"),
            index=chunk_index,
            text=_required_string(payload, "text", f"{chunk_label}.text"),
            token_count=token_count,
            storage=storage,
            metadata=_optional_mapping(payload, "metadata", f"{chunk_label}.metadata"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "index": self.index,
            "text": self.text,
            "tokenCount": self.token_count,
            "storage": self.storage.to_payload() if self.storage else None,
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
    """LIS VectorizeRoutePayload plus PrismPipe vectorize options."""

    route_id: str
    document_id: str
    manifest_version: str | int
    destination: str
    quality_score: float
    document: DocumentReference
    chunks: list[ChunkReference]
    storage_references: list[StorageReference] = field(default_factory=list)
    correlation_id: str | None = None
    embedding_model: str | None = None
    classification: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    options: VectorizeOptions = field(default_factory=VectorizeOptions)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DocumentVectorizeInput":
        if not isinstance(payload, Mapping):
            raise DocumentVectorizeValidationError("document.vectorize payload must be an object")

        route_id = _required_string(payload, "routeId", "routeId")
        document_id = _required_string(payload, "documentId", "documentId")
        manifest_version = _required_manifest_version(payload, "manifestVersion")
        destination = _required_string(payload, "destination", "destination")
        if destination != "vectorize":
            raise DocumentVectorizeValidationError("destination must be 'vectorize'")

        quality_score = _required_number(payload, "qualityScore", "qualityScore")

        raw_document = payload.get("document")
        if not isinstance(raw_document, Mapping):
            raise DocumentVectorizeValidationError("document must be an object")
        document = DocumentReference.from_payload(raw_document)

        raw_chunks = payload.get("chunks")
        if not isinstance(raw_chunks, list) or not raw_chunks:
            raise DocumentVectorizeValidationError("chunks must be a non-empty list")

        chunks: list[ChunkReference] = []
        for index, raw_chunk in enumerate(raw_chunks):
            if not isinstance(raw_chunk, Mapping):
                raise DocumentVectorizeValidationError(f"chunks[{index}] must be an object")
            chunks.append(ChunkReference.from_payload(raw_chunk, index))

        raw_storage_references = payload.get("storageReferences", [])
        if not isinstance(raw_storage_references, list):
            raise DocumentVectorizeValidationError("storageReferences must be a list")

        storage_references: list[StorageReference] = []
        for index, raw_storage in enumerate(raw_storage_references):
            if not isinstance(raw_storage, Mapping):
                raise DocumentVectorizeValidationError(
                    f"storageReferences[{index}] must be an object"
                )
            storage_references.append(
                StorageReference.from_payload(raw_storage, f"storageReferences[{index}]")
            )

        metadata = _optional_mapping(payload, "metadata", "metadata")
        options = VectorizeOptions.from_payload(payload.get("options"))
        return cls(
            route_id=route_id,
            document_id=document_id,
            manifest_version=manifest_version,
            destination=destination,
            quality_score=quality_score,
            document=document,
            chunks=chunks,
            storage_references=storage_references,
            correlation_id=_optional_string(payload, "correlationId", "correlationId"),
            embedding_model=_optional_string(payload, "embeddingModel", "embeddingModel"),
            classification=payload.get("classification"),
            metadata=metadata,
            options=options,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "routeId": self.route_id,
            "documentId": self.document_id,
            "manifestVersion": self.manifest_version,
            "destination": self.destination,
            "qualityScore": self.quality_score,
            "correlationId": self.correlation_id,
            "embeddingModel": self.embedding_model,
            "classification": self.classification,
            "document": self.document.to_payload(),
            "chunks": [chunk.to_payload() for chunk in self.chunks],
            "storageReferences": [reference.to_payload() for reference in self.storage_references],
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
            "routeId": request.route_id,
            "manifestVersion": request.manifest_version,
            "correlationId": request.correlation_id,
            "embeddingModel": request.embedding_model,
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


def _required_int(payload: Mapping[str, Any], key: str, label: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentVectorizeValidationError(f"{label} must be an integer")
    return value


def _optional_int(payload: Mapping[str, Any], key: str, label: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentVectorizeValidationError(f"{label} must be an integer")
    return value


def _required_manifest_version(payload: Mapping[str, Any], key: str) -> str | int:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise DocumentVectorizeValidationError(f"{key} must be a non-empty string or integer")


def _required_number(payload: Mapping[str, Any], key: str, label: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, Real):
        raise DocumentVectorizeValidationError(f"{label} must be a number")
    return float(value)


def _optional_string(payload: Mapping[str, Any], key: str, label: str | None = None) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DocumentVectorizeValidationError(f"{label or key} must be a non-empty string")
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
