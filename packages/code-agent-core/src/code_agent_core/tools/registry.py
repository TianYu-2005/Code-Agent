"""Explicit immutable-registration registry for tools."""

from dataclasses import dataclass
from hashlib import sha256

from code_agent_llm import ModelToolSpec

from .base import Tool, ToolOrigin, ToolSpec
from .schema import CompiledToolSchema, ToolSchemaError, compile_schema


class ToolRegistryError(ValueError):
    """Raised when tool registration or lookup is invalid."""


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """Tool implementation bound to its validated registration snapshot."""

    tool: Tool
    spec: ToolSpec
    schema: CompiledToolSchema
    fingerprint: str
    revision: int


class ToolRegistry:
    """Register tools explicitly and reject conflicts or invalid schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._revision = 0

    def register(self, tool: Tool, *, origin: ToolOrigin) -> None:
        spec = tool.spec.model_copy(update={"origin": origin})
        if spec.name in self._tools:
            raise ToolRegistryError(f"tool already registered: {spec.name}")
        try:
            compiled = compile_schema(spec.input_schema)
        except ToolSchemaError as error:
            raise ToolRegistryError(f"invalid schema for tool: {spec.name}") from error
        self._revision += 1
        fingerprint = sha256(
            f"{spec.model_dump_json()}:{compiled.fingerprint}:{self._revision}".encode()
        ).hexdigest()
        self._tools[spec.name] = RegisteredTool(
            tool=tool,
            spec=spec,
            schema=compiled,
            fingerprint=fingerprint,
            revision=self._revision,
        )

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolRegistryError(f"unknown tool: {name}") from error

    def model_specs(self, names: frozenset[str] | None = None) -> tuple[ModelToolSpec, ...]:
        selected = self._tools if names is None else {name: self.get(name) for name in names}
        return tuple(
            ModelToolSpec(
                name=registered.spec.name,
                description=registered.spec.description,
                input_schema=registered.spec.input_schema,
            )
            for _, registered in sorted(selected.items())
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))
