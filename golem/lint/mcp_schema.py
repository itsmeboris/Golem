"""MCP tool schema definition and validator."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON Schema (for documentation/export purposes only)
# Validation is done programmatically below.
# ---------------------------------------------------------------------------

MCP_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "description", "inputSchema"],
    "properties": {
        "name": {
            "type": "string",
            "pattern": "^[a-zA-Z][a-zA-Z0-9_]{0,63}$",
            "maxLength": 64,
        },
        "description": {
            "type": "string",
            "maxLength": 1024,
        },
        "inputSchema": {
            "type": "object",
            "required": ["type", "properties"],
            "properties": {
                "type": {"type": "string", "const": "object"},
                "properties": {"type": "object"},
            },
        },
        "permissions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["resource", "access"],
                "properties": {
                    "resource": {
                        "type": "string",
                        "enum": ["filesystem", "network", "ui", "process"],
                    },
                    "access": {
                        "type": "string",
                        "enum": ["read", "write", "execute"],
                    },
                },
            },
        },
    },
    "additionalProperties": True,
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NAME_PATTERN = re.compile(MCP_TOOL_SCHEMA["properties"]["name"]["pattern"])

_perm_schema = MCP_TOOL_SCHEMA["properties"]["permissions"]["items"]["properties"]
_VALID_RESOURCES = frozenset(_perm_schema["resource"]["enum"])
_VALID_ACCESS_LEVELS = frozenset(_perm_schema["access"]["enum"])
del _perm_schema

_VALID_RESOURCES_STR = ", ".join(sorted(_VALID_RESOURCES))
_VALID_ACCESS_LEVELS_STR = ", ".join(sorted(_VALID_ACCESS_LEVELS))

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


def validate_tool_schema(tool: dict[str, Any]) -> list[str]:
    """Validate a tool dict against the MCP tool schema.

    Returns a list of violation messages.  An empty list means the tool is
    valid.
    """
    if not isinstance(tool, dict):
        return ["tool must be a dict/object"]

    violations: list[str] = []

    # ------------------------------------------------------------------
    # Required fields
    # ------------------------------------------------------------------
    for field in ("name", "description", "inputSchema"):
        if field not in tool:
            violations.append(f"missing required field: {field!r}")

    # ------------------------------------------------------------------
    # name constraints
    # ------------------------------------------------------------------
    if "name" in tool:
        name: str = tool["name"]
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
        desc: str = tool["description"]
        if not isinstance(desc, str):
            violations.append("description must be a string")
        else:
            if len(desc) > 1024:
                violations.append(
                    "description exceeds maximum length of 1024 characters"
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
        schema: dict[str, Any] = tool["inputSchema"]
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
        perms: list[Any] = tool["permissions"]
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
                    resource: str = entry["resource"]
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
                    access: str = entry["access"]
                    if not isinstance(access, str):
                        violations.append(f"{prefix}: access must be a string")
                    elif access not in _VALID_ACCESS_LEVELS:
                        violations.append(
                            f"{prefix}: invalid access {access!r},"
                            f" expected one of: {_VALID_ACCESS_LEVELS_STR}"
                        )

    logger.debug("validate_tool_schema found %s violation(s)", len(violations))
    return violations
