"""Python SDK - Decorators and utilities for easy node creation."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from prismpipe.core import (
    Intent,
    RequestEnvelope,
    Node,
    NodeResult,
    Pipeline,
    PipelineConfig,
    CapabilityRouter,
)

T = TypeVar("T")
F = Callable[..., Any]


def node(
    capability: str,
    version: str = "1.0.0",
    description: str = "",
    **node_kwargs: Any,
) -> Callable[[F], Node]:
    """Decorator to create a node from a function."""

    def decorator(func: F) -> Node:
        capability_ = capability
        version_ = version
        description_ = description

        class FunctionNode(Node):
            capability = capability_
            version = version_
            description = description_

            def __init__(self) -> None:
                super().__init__()
                self._func = func

            def process(self, envelope: RequestEnvelope) -> NodeResult:
                try:
                    result = self._func(envelope)
                    if isinstance(result, NodeResult):
                        return result
                    if isinstance(result, RequestEnvelope):
                        return NodeResult(envelope=result)
                    return NodeResult(envelope=envelope)
                except Exception as e:
                    envelope.terminate(str(e))
                    return NodeResult(envelope=envelope, success=False, error=str(e))

        node_instance = FunctionNode()
        for k, v in node_kwargs.items():
            setattr(node_instance, k, v)
        return node_instance

    return decorator


def enrich(
    capability: str,
    **kwargs: Any,
) -> Callable[[F], Node]:
    """Decorator for enrichment nodes."""

    def decorator(func: F) -> Node:
        capability_ = capability
        
        class EnrichFunctionNode(Node):
            capability = capability_

            def __init__(self) -> None:
                super().__init__()
                self._func = func

            def process(self, envelope: RequestEnvelope) -> NodeResult:
                try:
                    result = self._func(envelope.input, envelope.state)
                    envelope.state.update(result)
                    return NodeResult(envelope=envelope)
                except Exception as e:
                    return NodeResult(envelope=envelope, success=False, error=str(e))

        return EnrichFunctionNode()

    return decorator


def transform(
    capability: str,
    **kwargs: Any,
) -> Callable[[F], Node]:
    """Decorator for transform nodes."""

    def decorator(func: F) -> Node:
        capability_ = capability
        
        class TransformFunctionNode(Node):
            capability = capability_

            def __init__(self) -> None:
                super().__init__()
                self._func = func

            def process(self, envelope: RequestEnvelope) -> NodeResult:
                try:
                    result = self._func(envelope.input, envelope.state)
                    envelope.state["transformed"] = result
                    return NodeResult(envelope=envelope)
                except Exception as e:
                    return NodeResult(envelope=envelope, success=False, error=str(e))

        return TransformFunctionNode()

    return decorator


def validate(
    capability: str,
    **kwargs: Any,
) -> Callable[[F], Node]:
    """Decorator for validation nodes."""

    def decorator(func: F) -> Node:
        capability_ = capability
        
        class ValidateFunctionNode(Node):
            capability = capability_

            def __init__(self) -> None:
                super().__init__()
                self._func = func

            def process(self, envelope: RequestEnvelope) -> NodeResult:
                try:
                    errors = self._func(envelope.input, envelope.state)
                    if errors:
                        envelope.terminate(f"Validation failed: {errors}")
                        return NodeResult(envelope=envelope, success=False, error=str(errors))
                    envelope.state["validation_passed"] = True
                    return NodeResult(envelope=envelope)
                except Exception as e:
                    return NodeResult(envelope=envelope, success=False, error=str(e))

        return ValidateFunctionNode()

    return decorator


class PrismPipe:
    """Main SDK class for building PrismPipe pipelines."""

    def __init__(
        self,
        router: CapabilityRouter | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        self._router = router or CapabilityRouter()
        self._pipeline = Pipeline(self._router, config)
        self._start_capability: str | None = None

    @property
    def pipeline(self) -> Pipeline:
        return self._pipeline

    @property
    def router(self) -> CapabilityRouter:
        return self._router

    def node(
        self,
        capability: str,
        version: str = "1.0.0",
        description: str = "",
    ) -> Callable[[F], Node]:
        def decorator(func: F) -> Node:
            n = node(capability, version, description)(func)
            self._pipeline.register_node(n)
            return n
        return decorator

    def start(self, capability: str) -> "PrismPipe":
        self._start_capability = capability
        return self

    def execute(
        self,
        input_data: dict[str, Any] | RequestEnvelope,
        state: dict[str, Any] | None = None,
        start_capability: str | None = None,
    ):
        if isinstance(input_data, RequestEnvelope):
            envelope = input_data
        else:
            envelope = RequestEnvelope(
                intent=Intent.CUSTOM,
                input=input_data,
                state=state or {},
                next=start_capability or self._start_capability,
            )
        return self._pipeline.execute(envelope)


__all__ = ["node", "enrich", "transform", "validate", "PrismPipe"]
