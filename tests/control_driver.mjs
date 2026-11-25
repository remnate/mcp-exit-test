// Test-only adapter for the installed CLI's evidence checks.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { basename, dirname } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const mode = basename(dirname(fileURLToPath(import.meta.url)));
const raw = ["missing-initialize", "missing-notification", "empty-client-info",
             "scalar-notification", "response-in-request", "id-in-notification",
             "extended-handshake", "late-invalid-notification"].includes(mode);
let transport, current, previous, firstUuid;
let connects = 0;
let request = 0;

async function exchange(method, params, extra = {}) {
  const id = ++request;
  const reply = new Promise(resolve => {
    transport.onmessage = message => {
      if (message.id === id) resolve(message);
    };
  });
  await transport.send({ jsonrpc: "2.0", id, method, params, ...extra });
  return await reply;
}

async function connect(params) {
  if (++connects === 2 && mode === "stalled-successor") await new Promise(() => {});
  transport = new StdioClientTransport({...params, env: process.env, stderr: "ignore"});
  if (raw) {
    await transport.start();
    const notification = {jsonrpc: "2.0", method: "notifications/initialized"};
    const init = {protocolVersion: "2025-11-25", capabilities: {},
                  clientInfo: {name: "control", version: "1"}};
    const extra = {};
    if (mode === "empty-client-info") init.clientInfo = {};
    if (mode === "scalar-notification") notification.params = 42;
    if (mode === "response-in-request") extra.result = {};
    if (mode === "id-in-notification") notification.id = "unexpected";
    if (mode === "extended-handshake") {
      init.protocolVersion = connects === 1 ? "2025-06-18" : "2025-11-25";
      init.capabilities = {roots: {listChanged: true}, experimental: {"test/feature": {v: 1}}};
      init._meta = {progressToken: "ready", "test/context": ["fixture"]};
      init.clientInfo.icons = [{src: "data:image/png;base64,AA==", theme: "dark"}];
      init.clientInfo.custom = 1;
      notification.params = {_meta: {"test/context": null}, extra: []};
    }
    if (mode !== "missing-initialize") await exchange("initialize", init, extra);
    if (mode !== "missing-notification") await transport.send(notification);
  } else {
    current = new Client({name: "control", version: "1"});
    await current.connect(transport);
  }
}

async function who() {
  const params = {name: "who", arguments: {}};
  if (raw) {
    const response = await exchange("tools/call", params);
    return response.error ? {isError: true, content: []} : response.result;
  }
  const result = await (previous ?? current).callTool(params);
  if (mode === "unbound-uuid") {
    const value = JSON.parse(result.content[0].text);
    firstUuid ??= value.instance;
    value.instance = firstUuid;
    result.content[0].text = JSON.stringify(value);
  }
  return result;
}

const input = createInterface({input: process.stdin});
try {
  for await (const line of input) {
    const {id, method, params} = JSON.parse(line);
    try {
      let result = {};
      if (method === "connect") await connect(params);
      else if (method === "who") result = await who();
      else if (method === "close") {
        if (mode === "verified-stale" && !previous) previous = current;
        else if (raw) {
          if (mode === "late-invalid-notification" && connects === 2) {
            await transport.send({jsonrpc: "2.0", method: "notifications/initialized", params: 42});
            await exchange("ping", {});
          }
          await transport.close();
        }
        else await current.close();
        current = undefined;
      }
      console.log(JSON.stringify({id, result}));
    } catch {
      console.log(JSON.stringify({id, error: {message: "Control operation failed"}}));
    }
  }
} finally {
  if (raw && transport) await transport.close();
  if (current) await current.close();
  if (previous) await previous.close();
}
