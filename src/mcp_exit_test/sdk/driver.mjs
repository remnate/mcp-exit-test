import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createInterface } from "node:readline";

let client;
const input = createInterface({input: process.stdin});
try {
  for await (const line of input) {
    const {id, method, params} = JSON.parse(line);
    try {
      let result = {};
      if (method === "connect") {
        client = new Client({name: "exit-witness", version: "0.1.0"});
        await client.connect(new StdioClientTransport({...params, env: process.env, stderr: "ignore"}));
      } else if (method === "who") {
        result = await client.callTool({name: "who", arguments: {}});
      } else if (method === "close") {
        await client.close();
        client = undefined;
      } else {
        throw new Error("Unknown command");
      }
      console.log(JSON.stringify({id, result}));
    } catch {
      console.log(JSON.stringify({id, error: {message: "SDK operation failed"}}));
    }
  }
} finally {
  if (client) await client.close();
}
