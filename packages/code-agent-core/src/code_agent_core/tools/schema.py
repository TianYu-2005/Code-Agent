"""Closed-world JSON Schema compilation and tool argument validation."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.protocols import Validator
from pydantic import JsonValue
from referencing import Registry
from referencing.exceptions import Unresolvable

from code_agent_llm import ToolCall
from code_agent_llm.immutable import freeze_json

from .base import MAX_TOOL_ARGUMENT_BYTES

MAX_SCHEMA_BYTES = 65_536
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 2_048
MAX_ARGUMENT_DEPTH = 32
MAX_ARGUMENT_NODES = 4_096
MAX_CONTAINER_ITEMS = 1_024
_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_ALLOWED_SCHEMA_KEYWORDS = {
    "$defs",
    "$id",
    "$schema",
    "additionalProperties",
    "const",
    "default",
    "description",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "prefixItems",
    "properties",
    "required",
    "title",
    "type",
}


class ToolSchemaError(ValueError):
    """Raised when a tool publishes an unsafe or invalid schema."""


@dataclass(frozen=True, slots=True)
class CompiledToolSchema:
    """Validated schema and reusable local-only validator."""

    validator: Validator
    fingerprint: str


def _reject_retrieve(uri: str) -> NoReturn:
    raise ToolSchemaError(f"external JSON Schema reference is forbidden: {uri}")


def compile_schema(schema: Mapping[str, JsonValue]) -> CompiledToolSchema:
    """Compile a closed Draft 2020-12 object schema without external retrieval."""
    try:
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise ToolSchemaError("tool schema is not valid JSON") from error
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise ToolSchemaError("tool schema exceeds the size limit")
    nodes = [0]
    _validate_schema_object(schema, depth=0, nodes=nodes, root=True)
    schema_dict = dict(schema)
    try:
        Draft202012Validator.check_schema(schema_dict)
    except SchemaError as error:
        raise ToolSchemaError("tool schema is invalid") from error
    registry = cast(
        Registry[bool | Mapping[str, Any]],
        cast(Any, Registry)(retrieve=_reject_retrieve),
    )
    frozen_schema = cast(Mapping[str, Any], freeze_json(schema_dict))
    return CompiledToolSchema(
        validator=Draft202012Validator(frozen_schema, registry=registry),
        fingerprint=sha256(encoded).hexdigest(),
    )


def validate_arguments(
    call: ToolCall,
    compiled: CompiledToolSchema,
) -> tuple[dict[str, JsonValue], str | None]:
    """Parse bounded JSON and validate it against a precompiled schema."""
    if len(call.arguments_json) > MAX_TOOL_ARGUMENT_BYTES:
        return {}, "tool arguments exceed the size limit"
    try:
        encoded = call.arguments_json.encode("utf-8")
    except UnicodeEncodeError:
        return {}, "tool arguments are not valid UTF-8"
    if len(encoded) > MAX_TOOL_ARGUMENT_BYTES:
        return {}, "tool arguments exceed the size limit"
    try:
        value = json.loads(
            call.arguments_json,
            parse_constant=lambda value: _reject_constant(value),
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return {}, "tool arguments are not valid JSON"
    if not isinstance(value, dict):
        return {}, "tool arguments must be a JSON object"
    try:
        _validate_json_tree(value, depth=0, nodes=[0])
        compiled.validator.validate(value)
    except (ToolSchemaError, ValidationError, Unresolvable, RecursionError, ValueError):
        return {}, "tool arguments do not match the input schema"
    return value, None


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _validate_schema_object(
    schema: Mapping[str, object],
    *,
    depth: int,
    nodes: list[int],
    root: bool = False,
) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise ToolSchemaError("JSON Schema nesting exceeds the depth limit")
    nodes[0] += 1
    if nodes[0] > MAX_SCHEMA_NODES:
        raise ToolSchemaError("JSON Schema exceeds the complexity limit")
    dialect = schema.get("$schema")
    if dialect is not None and dialect != _DRAFT_2020_12:
        raise ToolSchemaError("only JSON Schema Draft 2020-12 is supported")
    unsupported = set(schema).difference(_ALLOWED_SCHEMA_KEYWORDS)
    if unsupported:
        raise ToolSchemaError(f"unsupported JSON Schema keyword: {min(unsupported)}")
    if "$id" in schema:
        raise ToolSchemaError("JSON Schema identifiers are forbidden")
    schema_type = schema.get("type")
    includes_object = schema_type == "object" or (
        isinstance(schema_type, list) and "object" in schema_type
    )
    if root and schema_type != "object":
        raise ToolSchemaError("root tool schema must have type object")
    if not root and schema_type is None and "$ref" not in schema:
        raise ToolSchemaError("nested schemas must declare a concrete type")
    if includes_object and schema.get("additionalProperties") is not False:
        raise ToolSchemaError("object schemas must reject undeclared properties")
    if schema_type == "array" and "items" not in schema and "prefixItems" not in schema:
        raise ToolSchemaError("array schemas must constrain their items")
    for map_keyword in ("properties", "$defs"):
        children = schema.get(map_keyword)
        if children is not None:
            if not isinstance(children, dict):
                raise ToolSchemaError(f"{map_keyword} must be an object")
            if len(children) > MAX_CONTAINER_ITEMS:
                raise ToolSchemaError(f"{map_keyword} has too many entries")
            for child in children.values():
                _validate_child_schema(child, depth=depth + 1, nodes=nodes)
    items = schema.get("items")
    if items is not None:
        _validate_child_schema(items, depth=depth + 1, nodes=nodes)
    prefix_items = schema.get("prefixItems")
    if prefix_items is not None:
        if not isinstance(prefix_items, list) or len(prefix_items) > 64:
            raise ToolSchemaError("prefixItems must be a bounded array")
        for child in prefix_items:
            _validate_child_schema(child, depth=depth + 1, nodes=nodes)


def _validate_child_schema(child: object, *, depth: int, nodes: list[int]) -> None:
    if isinstance(child, bool):
        raise ToolSchemaError("boolean child schemas are forbidden")
    if not isinstance(child, dict):
        raise ToolSchemaError("nested JSON Schema must be an object or boolean")
    _validate_schema_object(child, depth=depth, nodes=nodes)


def _validate_json_tree(value: object, *, depth: int, nodes: list[int]) -> None:
    if depth > MAX_ARGUMENT_DEPTH:
        raise ToolSchemaError("JSON nesting exceeds the depth limit")
    nodes[0] += 1
    if nodes[0] > MAX_ARGUMENT_NODES:
        raise ToolSchemaError("JSON arguments exceed the complexity limit")
    if isinstance(value, float) and not isfinite(value):
        raise ToolSchemaError("JSON numbers must be finite")
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise ToolSchemaError("JSON object has too many properties")
        for item in value.values():
            _validate_json_tree(item, depth=depth + 1, nodes=nodes)
    elif isinstance(value, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise ToolSchemaError("JSON array has too many items")
        for item in value:
            _validate_json_tree(item, depth=depth + 1, nodes=nodes)
