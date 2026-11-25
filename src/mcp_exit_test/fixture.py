"""Private bounded synthetic server. All child pipes are independent of MCP."""

import json
import signal
import socket
import subprocess
import sys
import time
import uuid


def reject_non_json_number(value):
    raise ValueError("Non-JSON numeric constant")


def response_id(message):
    value = message.get("id")
    if type(value) in (str, int) or (isinstance(value, float) and value.is_integer()):
        return value
    return None


def error(request_id, code, message):
    reply = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    print(json.dumps(reply), flush=True)


def main():
    path, nonce, generation, policy, *child_id = sys.argv[1:]
    instance = child_id[0] if child_id else str(uuid.uuid4())
    identity = {"instance": instance, "generation": int(generation)}

    def event(kind, **extra):
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(2)
            conn.connect(path)
            data = dict(identity, nonce=nonce, kind=kind, **extra)
            conn.sendall(json.dumps(data).encode() + b"\n")
            if conn.recv(1) != b"+":
                raise RuntimeError("Fixture registration rejected")

    signal.alarm(75)
    if child_id:
        event("child")
        time.sleep(70)
        return
    from mcp_exit_test.handshake import SUPPORTED_PROTOCOLS, valid_handshake

    event("server")
    child = subprocess.Popen(
        [sys.executable, __file__, *sys.argv[1:], instance],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    version = None
    ready = False
    rejected = False
    try:
        for line in sys.stdin:
            try:
                msg = json.loads(line, parse_constant=reject_non_json_number)
            except ValueError:
                error(None, -32700, "Invalid JSON")
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
                error(None, -32600, "Invalid request")
                continue
            method = msg["method"]
            if method == "notifications/initialized":
                if (
                    rejected
                    or version is None
                    or ready
                    or not valid_handshake(msg, version, method)
                ):
                    rejected = True
                    ready = False
                    event("handshake_rejected", method=method)
                    if "id" in msg:
                        error(response_id(msg), -32600, "Invalid initialized notification")
                else:
                    event("initialized", protocol=version)
                    ready = True
                continue
            if method == "initialize":
                params = msg.get("params")
                offered = params.get("protocolVersion") if isinstance(params, dict) else None
                selected = offered if offered in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[-1]
                if rejected or version is not None or not valid_handshake(msg, selected, method):
                    rejected = True
                    ready = False
                    event("handshake_rejected", method=method)
                    if "id" in msg:
                        error(response_id(msg), -32600, "Invalid initialize request")
                    continue
                version = selected
                result = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "exit-probe", "version": "0.1.0"},
                }
            elif "id" not in msg:
                continue
            elif method == "ping":
                result = {}
            elif not ready:
                error(msg["id"], -32002, "Fixture handshake incomplete")
                continue
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "who",
                            "description": "Return fixture identity",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            elif method == "tools/call" and msg["params"]["name"] == "who":
                event("who_called")
                result = {"content": [{"type": "text", "text": json.dumps(identity)}]}
            else:
                error(msg["id"], -32601, "Method unavailable")
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
            if method == "initialize":
                event("initialize_response", protocol=version)
        event("eof")
    finally:
        if policy == "clean":
            child.terminate()
            child.wait(timeout=2)


if __name__ == "__main__":
    main()
