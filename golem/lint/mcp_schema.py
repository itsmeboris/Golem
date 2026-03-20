"""MCP tool schema definition and validator."""

import logging
import re
from typing import Any, TypeGuard

from golem.types import McpInputSchemaDict, McpToolDict, ToolPermissionDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NAME_RE = r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$"
_NAME_PATTERN = re.compile(_NAME_RE)
_NAME_MAX_LENGTH = 64
_DESCRIPTION_MAX_LENGTH = 1024
_VALID_RESOURCES: frozenset[str] = frozenset({"filesystem", "network", "ui", "process"})
_VALID_ACCESS_LEVELS: frozenset[str] = frozenset({"read", "write", "execute"})

_VALID_RESOURCES_STR = ", ".join(sorted(_VALID_RESOURCES))
_VALID_ACCESS_LEVELS_STR = ", ".join(sorted(_VALID_ACCESS_LEVELS))

_CAMEL_TO_SNAKE: dict[str, str] = {
    "inputSchema": "input_schema",
}

# ---------------------------------------------------------------------------
# JSON Schema (for documentation/export purposes only)
# Validation is done programmatically below.
# ---------------------------------------------------------------------------

MCP_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": sorted(McpToolDict.__required_keys__),  # pylint: disable=no-member
    "properties": {
        "name": {
            "type": "string",
            "pattern": _NAME_RE,
            "maxLength": _NAME_MAX_LENGTH,
        },
        "description": {
            "type": "string",
            "maxLength": _DESCRIPTION_MAX_LENGTH,
        },
        "inputSchema": {
            "type": "object",
            "required": sorted(
                McpInputSchemaDict.__required_keys__  # pylint: disable=no-member
            ),
            "properties": {
                "type": {"type": "string", "const": "object"},
                "properties": {"type": "object"},
            },
        },
        "permissions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": sorted(
                    ToolPermissionDict.__required_keys__  # pylint: disable=no-member
                ),
                "properties": {
                    "resource": {
                        "type": "string",
                        "enum": sorted(_VALID_RESOURCES),
                    },
                    "access": {
                        "type": "string",
                        "enum": sorted(_VALID_ACCESS_LEVELS),
                    },
                },
            },
        },
    },
    "additionalProperties": True,
}

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+previous\b",
        r"(?:^|\n)\s*system\s*:",
        r"<\|",
        r"(?:^|\n)\s*IMPORTANT\s*:",
        r"\byou\s+must\b.*\b(?:comply|obey|follow|ignore)\b",
        r"\boverride\b.*\b(?:instruction|prompt|rule|setting)s?\b",
    ]
]


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_tool_schema(tool: object) -> list[str]:
    """Validate a tool dict against the MCP tool schema.

    Accepts any value, including raw ``.json()`` output from HTTP clients.
    Returns a list of violation messages.  An empty list means the tool is
    valid.

    Non-dict values immediately return ``["tool must be a dict/object"]``.
    After the isinstance check, mypy narrows ``object`` to ``dict``, so all
    subsequent field accesses are type-safe.

    Note: ``inputSchema`` uses camelCase per the MCP protocol convention.
    If a caller passes the snake_case spelling ``input_schema``, the
    validator produces a hint directing them to the camelCase form.
    """
    if not isinstance(tool, dict):
        return ["tool must be a dict/object"]

    violations: list[str] = []

    # ------------------------------------------------------------------
    # Required fields
    # ------------------------------------------------------------------
    for field in McpToolDict.__required_keys__:  # pylint: disable=no-member
        if field not in tool:
            snake_alias = _CAMEL_TO_SNAKE.get(field)
            if snake_alias and snake_alias in tool:
                violations.append(
                    f"missing required field: {field!r}"
                    f" (found {snake_alias!r} — MCP protocol uses camelCase)"
                )
            else:
                violations.append(f"missing required field: {field!r}")

    # ------------------------------------------------------------------
    # name constraints
    # ------------------------------------------------------------------
    if "name" in tool:
        name = tool["name"]
        if not isinstance(name, str):
            violations.append("name must be a string")
        elif not _NAME_PATTERN.match(name):
            violations.append(
                "name must start with a letter and contain only letters, digits, "
                "and underscores (max 64 characters)"
            )

    # ------------------------------------------------------------------
    # description constraints
    # ------------------------------------------------------------------
    if "description" in tool:
        desc = tool["description"]
        if not isinstance(desc, str):
            violations.append("description must be a string")
        else:
            if len(desc) > _DESCRIPTION_MAX_LENGTH:
                violations.append(
                    f"description exceeds maximum length of {_DESCRIPTION_MAX_LENGTH} characters"
                )
            for pattern in _INJECTION_PATTERNS:
                if pattern.search(desc):
                    violations.append(
                        "description contains a prompt injection pattern: "
                        f"{pattern.pattern!r}"
                    )

    # ------------------------------------------------------------------
    # inputSchema constraints
    # ------------------------------------------------------------------
    if "inputSchema" in tool:
        schema = tool["inputSchema"]
        if not isinstance(schema, dict):
            violations.append("inputSchema must be an object/dict")
        else:
            if "type" not in schema:
                violations.append("inputSchema is missing required field 'type'")
            elif schema["type"] != "object":
                violations.append(
                    f"inputSchema.type must be 'object', got {schema['type']!r}"
                )

            if "properties" not in schema:
                violations.append("inputSchema is missing required field 'properties'")
            elif not isinstance(schema["properties"], dict):
                violations.append("inputSchema.properties must be an object/dict")

    # ------------------------------------------------------------------
    # permissions constraints (optional field)
    # ------------------------------------------------------------------
    if "permissions" in tool:
        perms = tool["permissions"]
        if not isinstance(perms, list):
            violations.append("permissions must be a list")
        else:
            for idx, entry in enumerate(perms):
                prefix = f"permissions[{idx}]"
                if not isinstance(entry, dict):
                    violations.append(f"{prefix}: entry must be a dict")
                    continue
                if "resource" not in entry:
                    violations.append(f"{prefix}: missing required field 'resource'")
                else:
                    resource = entry["resource"]
                    if not isinstance(resource, str):
                        violations.append(f"{prefix}: resource must be a string")
                    elif resource not in _VALID_RESOURCES:
                        violations.append(
                            f"{prefix}: invalid resource {resource!r},"
                            f" expected one of: {_VALID_RESOURCES_STR}"
                        )
                if "access" not in entry:
                    violations.append(f"{prefix}: missing required field 'access'")
                else:
                    access = entry["access"]
                    if not isinstance(access, str):
                        violations.append(f"{prefix}: access must be a string")
                    elif access not in _VALID_ACCESS_LEVELS:
                        violations.append(
                            f"{prefix}: invalid access {access!r},"
                            f" expected one of: {_VALID_ACCESS_LEVELS_STR}"
                        )

    logger.debug("validate_tool_schema found %s violation(s)", len(violations))
    return violations


def is_valid_mcp_tool(tool: object) -> TypeGuard[McpToolDict]:
    """Type-guard wrapper: True when *tool* passes MCP schema validation.

    Accepts any value, including raw ``.json()`` output from HTTP clients.
    When this returns ``True``, type checkers narrow *tool* to
    :class:`~golem.types.McpToolDict`.
    """
    return not validate_tool_schema(tool)
