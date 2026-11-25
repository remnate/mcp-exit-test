# Measured Linux release checks

These are local runs of the 0.1.0 wheel, launched outside the checkout. The host and SDK baseline, survivor and interruption cases use a 5 s grace period. The focused regression controls use 0.6 s. Each link opens a measured report. Its directory also contains the corresponding JUnit result. The release check observed the same unrelated sentinel after every case and independently checked the kernel birth identity of each registered process and the driver after cleanup. [Release environment and sentinel receipt](release.json)

| Case | Result at the deadline |
| --- | --- |
| [Codex, direct](codex-direct-clean-eof/result.json) | Four generations in one thread. Each of three reconnects retired its old pair. The four pairs and driver were gone after EOF. |
| [Codex, local npm wrapper](codex-npm-clean-eof/result.json) | Four distinct tool UUIDs. Each new pair was active at the reconnect deadline and answered a further call. All eight registered identities and the driver were gone after EOF. |
| [Codex, host interruption](codex-direct-clean-interrupt/result.json) | One registered pair and its host disappeared after SIGTERM. |
| [SDK, direct](sdk-direct-clean-eof/result.json) and [SDK, local npm wrapper](sdk-npm-clean-eof/result.json) | Both clean runs closed and replaced a client/transport pair, proved the successor call, explicitly disconnected and closed the driver. |
| [SDK, deliberate survivor](sdk-direct-survive-eof/result.json) | The server was gone. Its separately sessioned child was active with a new parent. The CLI recorded the failure, verified cleanup and returned 1. |
| [Interrupted observer](interrupted-observer/result.json) | SIGTERM after a verified tool call produced a strict inconclusive result and successful teardown. |
| [Hung readiness](hung-connect/result.json) and [hung close](hung-close/result.json) | A test-only SDK adapter withheld its response. Each 10 s request timeout produced a strict inconclusive result and successful teardown. |

## Focused regression controls

| Input or behavior | Installed CLI result |
| --- | --- |
| [Initialized notification without a request](missing-initialize/result.json) and [initialize response without the notification](missing-notification/result.json) | Both handshakes were incomplete. Each tool call was rejected, the product case was skipped and strict mode returned 2. |
| [Empty clientInfo](empty-client-info/result.json) and [scalar initialized params](scalar-notification/result.json) | Both review cases returned strict exit 2 and a skipped product case. The empty implementation object was rejected at initialize. The scalar notification was rejected after the initialize response. Neither fixture registered readiness or handled who. |
| [Initialize carrying a response field](response-in-request/result.json) and [initialized carrying a request ID](id-in-notification/result.json) | Each malformed JSON-RPC message was recorded as a rejected handshake. Both strict runs were inconclusive with successful cleanup. |
| [Malformed duplicate after the final tool call](late-invalid-notification/result.json) | The valid exchange and tool calls were observed before the extra notification. The fixture recorded the later rejection and the strict run returned 2 even though the retirement checks passed. |
| [Valid handshake extensions](extended-handshake/result.json) | A raw client negotiated the June revision, then the November revision across replacement. Both instances registered readiness and handled who. Their requests included experimental capabilities and metadata. |
| [Successor generation paired with predecessor UUID](unbound-uuid/result.json) | Both real servers completed their handshakes. The adapter changed the second reply. The observer could not bind it to the reporting server, so it returned strict inconclusive. |
| [Two calls routed to the live predecessor](verified-stale/result.json) | Fixture-side events confirmed both calls reached the old server. The CLI returned 1 with a JUnit failure. |
| [Successor timeout in strict mode](survivor-timeout-strict/result.json) and [default mode](survivor-timeout-default/result.json) | The old child remained active at the retirement deadline. The second connect timed out later. Both runs recorded both observations, returned 1 and failed the product case in JUnit. |
| [Readable pidfd with hangup masked](legacy-poll/result.json) | A clean SDK reconnect and final disconnect passed with a test shim masking POLLHUP. The kernel still provided genuine pidfds, procfs and fdinfo. This simulates legacy polling on the measured kernel. |

The legacy control masks only POLLHUP on real pidfds. A separate kernel-backed unit test holds an exited child as a zombie, reaps it, and checks both states through the same descriptor. Linux 6.8 implements the fdinfo Pid field used by this fallback. Execution on that kernel remains unverified. [Linux 6.8 pidfd implementation](https://github.com/torvalds/linux/blob/v6.8/kernel/fork.c)

In the npm-host run the four UUID prefixes were `f448a16a`, `7c77b1f2`, `0e1733aa` and `eb1fa1cc`. At the third reconnect, the observer had seen both retiring identities gone by 0.10037 s. The deadline sample at 5.00051 s found the successor server and child active. The subsequent who call returned `eb1fa1cc` again. After control-input EOF the host exited with status 0.

The Codex fixture negotiated MCP 2025-06-18 and the JS SDK fixture negotiated 2025-11-25. The npm host report includes launcher ancestry at registration. Its verdict covers the registered fixture pairs and direct driver exit. Server-side EOF events, when present, appear in the timeline. Process disappearance alone does not establish the termination mechanism.

Earlier exploratory trials motivated these cases. The checked-in JSON comes from the packaged tool and uses its own UUIDs and deadlines. Product timings exclude cleanup. Each example retains the synthetic PIDs and kernel ticks needed to interpret identity comparisons. The reports omit account data, command lines, environment values, home paths and raw boot IDs.
