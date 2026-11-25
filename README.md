# MCP Exit Test

Run a headless Linux MCP host through reconnect and shutdown in CI. See which registered test-owned stdio processes survived each transition. Each fixture returns its UUID and generation from a real tool call. A second process observes both its server and its tagged child.

## Run from a checkout

Use Linux with readable procfs, pidfd signaling and evidence of reaping through pidfd hangup or pidfd fdinfo, Python 3.11+, Node.js 24 and npm. The measured Codex version is 0.171.0. From the repository root, in a POSIX shell:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
npm install --prefix .host --no-audit --no-fund @openai/codex@0.171.0
export PATH="$PWD/.host/node_modules/.bin:$PWD/.venv/bin:$PATH"
mcp-exit-test --driver codex --launcher direct --strict --out exit-results-direct
mcp-exit-test --driver codex --launcher npm --strict --out exit-results-npm
```

Each command starts one app-server and one ephemeral thread, makes an initial call, applies three private registration changes with config/mcpServer/reload, and closes the host control input. Discovery and calls use the same thread ID. The npm profile installs a bundled local executable into the run's private directory and launches it with npm exec --offline --yes=false. Runs use a synthetic HOME, an empty credential environment and a loopback model endpoint. No model turn or browser is required.

For a shorter host demo use `--reconnects 1`. To exercise host interruption independently:

```sh
mcp-exit-test --driver codex --exit interrupt --reconnects 0 --strict --out exit-results-interrupt
```

## Reproduce the positive control

Prepare the optional pinned JS SDK once. Use the same shell and checkout:

```sh
cp -R src/mcp_exit_test/sdk .sdk
npm ci --prefix .sdk --ignore-scripts --no-audit --no-fund
mcp-exit-test --driver sdk --sdk-dir .sdk --strict --out exit-results-sdk
mcp-exit-test --driver sdk --sdk-dir .sdk --policy survive --reconnects 0 --strict --out exit-results-control
```

The last command should exit **1**. Read the explicit SDK disconnect check in result.json. Its server should be gone, its child active, and cleanup.ok true. The child starts a separate session and closes its inherited transport pipes. The control exercises a fixture policy and makes no finding about a third-party server. With reconnects enabled, the SDK adapter closes its Client, creates a fresh Client and StdioClientTransport in the same driver, and calls who again.

## Read the evidence

Every output directory contains result.json and junit.xml. The CLI prints PID, kernel start ticks, UUID, generation and deadline states. A role key has the form generation/UUID/role. JSON records relative monotonic seconds, request/response pairs, initialization, tool calls, retirement checks, the pre-cleanup verdict and teardown status. JUnit contains a product case and a separate cleanup case.

| Exit | Meaning |
| --- | --- |
| 0 | Pass, or inconclusive without --strict |
| 1 | Observed contract violation |
| 2 | Strict inconclusive, observer interruption, or invalid CLI usage |
| 3 | Harness or cleanup error |

The fixture validates the incoming initialize request and initialized notification against the packaged MCP schemas, including their JSON-RPC envelopes. An offered supported version selects its own schema. For an unknown version string the fixture checks the request against its latest supported version and offers that version in its response. Required fields, known capabilities, request metadata and declared URI fields are checked. Extension properties remain available where the selected schema permits them. A notification must omit the request ID, and handshake messages carrying response-only fields are rejected. [June 2025 schema](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2025-06-18/schema/2025-06-18/schema.json) · [November 2025 schema](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2025-11-25/schema/2025-11-25/schema.json)

The observer requires evidence of the fixture’s initialize response followed by a valid notification on that connection. A rejected handshake prevents that instance from becoming ready. The schema check covers these two inbound handshake messages. Other MCP operations, extension behavior and host-wide protocol conformance are outside its scope. The observer binds each who reply to a fresh call event from that registered server. An unbound reply is inconclusive. Two calls to the same verified stale fixture fail the transition.

Use --strict in CI. Inconclusive results map to skipped in JUnit even in strict mode. Each command replaces the two files in its chosen output directory.

## Scope and timing

The registered server and child must disappear, including their process-table entries. The driver may stay alive across reconnects and must exit at final close. Launcher ancestry is recorded while the server is alive. Helpers, pooled browsers, unregistered descendants and whole-host containment are outside v0.1 coverage. This scenario exercises configuration reconnect and transport teardown. GUI closure and network recovery need separate adapters.

A private Unix socket authenticates registration using a per-run nonce, kernel peer PID/UID and ancestry to the launched driver. The observer opens pidfds, cross-checks kernel start ticks, and records PPID, process group, session and state. A salted boot tag allows correlation within a report. A pidfd hangup or a replaced birth identity is gone. On a kernel that reports exit through POLLIN alone, the observer checks the same pidfd’s /proc/self/fdinfo entry. A Pid field of -1 proves that its task has disappeared. A missing /proc entry with a live pidfd is unknown. A defunct process is zombie. Unreadable state is unknown. The registered-pair disappearance contract includes reaping; a zombie fails separately from a live survivor. Roles describe fixture expectations and do not assign responsibility for a leak.

Requests have a 10 s budget. A 5 s retirement window starts at the lifecycle response, host EOF request or SIGTERM delivery. The observer samples at 100 ms intervals during successor setup and requires three consecutive gone samples including the deadline sample. A verified retirement failure determines the product verdict even when successor setup times out. JSON records the successor issue separately. The observer checks the current pair at that deadline and calls it again afterward. The window is configurable from 0.5 to 5 s. Self-expiry is 70 s for the sleeper and 75 s for its server. The observer reserves 5 s of its 90 s scenario budget for teardown. TERM and KILL target verified handles with up to two seconds per phase. Ctrl-C and SIGTERM invoke cleanup. SIGKILL cannot run observer cleanup. A missed deadline sample, unsupported host method or unreadable identity limits the verdict. The MCP lifecycle defines closure of stdio and allows signal escalation. The observation window is a test parameter. [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)

See [measured examples](examples/README.md), the [pilot plan](docs/launch.md) and [contributor instructions](CONTRIBUTING.md).
