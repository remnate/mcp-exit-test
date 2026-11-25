The Python observer, fixture, local wrapper and JavaScript driver are original
MIT-licensed code. The wheel includes an npm dependency lockfile. The optional
reference driver loads the separately installed Model Context Protocol
TypeScript SDK (MIT). npm ci retains each dependency's distributed licenses
and notices in node_modules. Codex CLI and Node.js are external executables.

The files in src/mcp_exit_test/schemas contain the upstream handshake and
JSON-RPC envelope definitions, with their transitive references, extracted
from the MCP 2025-06-18 and 2025-11-25 schema.json files. LICENSE-MCP
contains their MIT license and copyright notice. Python package installation
also installs jsonschema and the RFC 3986 URI format checker.
