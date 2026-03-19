"""MCP tool schema definition and validator."""

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON Schema (for documentation/export purposes only)
# Validation is done programmatically below.
# ---------------------------------------------------------------------------

MCP_TOOL_SCHEMA: dict = {
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
    },
    "additionalProperties": True,
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore previous",
        r"system:",
        r"<\|",
        r"IMPORTANT:",
        r"you must",
        r"override",
    ]
]


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_tool_schema(tool: dict) -> list[str]:
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

    logger.debug("validate_tool_schema found %s violation(s)", len(violations))
    return violations
