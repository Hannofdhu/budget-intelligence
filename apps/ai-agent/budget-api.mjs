#!/usr/bin/env node
// The only budget interface available inside the isolated runtime is a
// read-only, allowlisted host broker. This CLI never accepts an arbitrary URL.
import http from "node:http";
import { mkdir, open, realpath } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";

const resources = [
  "help",
  "fy27-budget", "fy27-forecast", "fy28-budget", "next-year-catalog",
  "fy27-forecast-versions", "fy28-budget-versions",
];
const help = {
  usage: "budget-api list | budget-api get RESOURCE [--version ID] [--output FILE]",
  resources,
  examples: ["budget-api get fy27-budget", "budget-api get fy28-budget --output budget.json"],
  permissions: "仅查询产品已保存数据；不能修改预算、刷新预测或执行定版。",
};
async function main() {
  const args = process.argv.slice(2);
  if (!args.length || ["help", "--help", "-h"].includes(args[0])) {
    console.log(JSON.stringify(help, null, 2)); return;
  }
  if (args[0] === "list" && args.length === 1) {
    console.log(JSON.stringify(resources, null, 2)); return;
  }
  const resource = args[1];
  if (args[0] !== "get" || !resources.includes(resource)) throw new Error("未知资源；运行 budget-api help");
  let version = null;
  let output = null;
  for (let i = 2; i < args.length; i += 2) {
    const key = args[i], value = args[i + 1];
    if (!value || !["--version", "--output"].includes(key)) throw new Error("参数无效");
    if (key === "--version") version = value;
    else output = value;
  }
  if (version && !["fy27-forecast", "fy28-budget"].includes(resource)) {
    throw new Error("该资源不支持版本查询");
  }
  if (version && !/^[A-Za-z0-9_-]{1,128}$/.test(version)) throw new Error("版本标识无效");
  const requested = version ? `${resource}-version:${version}` : resource;
  const url = `/budget/${encodeURIComponent(requested)}`;
  const data = await new Promise((resolve, reject) => {
    const request = http.get({ hostname: "127.0.0.1", port: 4081, path: url }, response => {
      const chunks = [];
      let size = 0;
      response.on("data", chunk => {
        size += chunk.length;
        if (size > 64 * 1024 * 1024) { request.destroy(new Error("查询结果过大")); return; }
        chunks.push(chunk);
      });
      response.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        if ((response.statusCode || 500) >= 400) reject(new Error(`查询失败 (${response.statusCode})：${text}`));
        else resolve(text);
      });
    });
    request.setTimeout(60_000, () => request.destroy(new Error("查询超时")));
    request.on("error", reject);
  });
  if (!output) { process.stdout.write(data + "\n"); return; }
  const target = path.resolve("/workspace", output);
  if (!target.startsWith("/workspace/")) throw new Error("只能保存到 AI 工作目录");
  await mkdir(path.dirname(target), { recursive: true });
  const parent = await realpath(path.dirname(target));
  if (!parent.startsWith("/workspace/") && parent !== "/workspace") throw new Error("不能写出 AI 工作目录");
  const file = await open(target, constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC | constants.O_NOFOLLOW, 0o600);
  try { await file.writeFile(data, { encoding: "utf8" }); }
  finally { await file.close(); }
  console.log(`已保存 ${path.relative("/workspace", target)}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
