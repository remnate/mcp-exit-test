"""Validate handshake messages against packaged definitions from the two MCP releases."""

import json
from functools import cache
from pathlib import Path

from jsonschema import FormatChecker
from jsonschema.validators import validator_for

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-11-25")


@cache
def validator(version, method):
    path = Path(__file__).with_name("schemas") / f"{version}.json"
    schema = json.loads(path.read_text())
    definitions = "$defs" if "$defs" in schema else "definitions"
    if method == "initialize":
        types = ("JSONRPCRequest", "InitializeRequest")
    else:
        types = ("JSONRPCNotification", "InitializedNotification")
    schema["allOf"] = [{"$ref": f"#/{definitions}/{name}"} for name in types]
    return validator_for(schema)(schema, format_checker=FormatChecker(formats=["uri"]))


def valid_handshake(message, version, method):
    # The wire schema allows extension keys. Response discriminators and a
    # notification's absent request ID also carry JSON-RPC message semantics.
    if not isinstance(message, dict) or "result" in message or "error" in message:
        return False
    if method == "notifications/initialized" and "id" in message:
        return False
    return validator(version, method).is_valid(message)
