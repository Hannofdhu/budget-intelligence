import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import * as React from "react";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetCockpitPage.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "react/jsx-runtime"], loader: { ".css": "empty" }, logLevel: "silent",
  plugins: [{ name: "cache-fixture", setup(builder) { builder.onResolve({ filter: /\/budgetReadCache(?:\.ts)?$/ }, () => ({ path: "cockpit-cache-fixture", external: true })); } }] });
const data = { read_only: true, unit: "yuan", fiscal_year: 2027, next_fiscal_year: 2028, sources: { budget: {id:"b",status:"FINALIZED"}, actual: {id:"p26"}, forecast: {id:"p26",sequence:26}, next_budget: {id:"n25",sequence:25} }, rows: [], notes: [], definitions: {} };
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== "object" ? [] : [tree, ...nodes(tree.props?.children)];
const text = tree => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree);

test("same-source browser back invalidates an in-flight latest read instead of silently replacing historical pins", async () => {
  const original = { window: globalThis.window, location: globalThis.location, history: globalThis.history, fetch: globalThis.fetch };
  const actor = { userId:"u",departmentId:"d",roles:[] }, slots = [], effects = [], listeners = new Map(), replacements = [];
  let cursor = 0, resolveLatest, latest = false;
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], next => { slots[i] = typeof next === "function" ? next(slots[i]) : next; }]; },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useMemo(fn) { cursor++; return fn(); },
    useEffect(fn) { const i = cursor++; if (!(i in slots)) { slots[i] = true; effects.push(fn); } },
  };
  const cache = { peekBudgetSession:()=>actor, getBudgetSession:async()=>actor, peekBudgetRead:()=>data,
    budgetReadKey:(_,path)=>path, invalidateBudgetReads(){}, readBudgetResource:(_,loader)=>loader(new AbortController().signal) };
  const loaded = { exports:{} };
  new Function("require","module","exports",compiled.outputFiles[0].text)(id=>id==="react"?hooks:id==="cockpit-cache-fixture"?cache:require(id),loaded,loaded.exports);
  const search = "?forecast_version=p26&nyVersion=n25&cc_group=campus";
  globalThis.location = { search, pathname:"/budget/cockpit", origin:"http://budget.test" };
  globalThis.history = { state:null, replaceState(_,__,url){ replacements.push(url); }, pushState(){} };
  globalThis.window = { addEventListener:(name,fn)=>listeners.set(name,fn), removeEventListener:name=>listeners.delete(name) };
  globalThis.fetch = async()=> { if(latest) return new Promise(resolve=>{resolveLatest=resolve;}); return new Response(JSON.stringify(data)); };
  const render = ()=>{cursor=0;return loaded.exports.BudgetCockpitPage();};
  const settle = async()=>{await new Promise(resolve=>setImmediate(resolve));};
  let cleanups=[];
  try {
    render(); cleanups = effects.map(fn=>fn()); await settle();
    let tree=render(); const nextSelector=nodes(tree).find(n=>n.props?.["aria-label"]==="下一层下钻维度");
    assert.equal(nextSelector.props.value,"track","current campus grouping cannot promise to drill again into campus");
    latest=true; nodes(tree).find(n=>n.type==="button"&&text(n)==="读取最新成果").props.onClick(); await settle();
    listeners.get("popstate")(); render();
    resolveLatest(new Response(JSON.stringify({...data,sources:{...data.sources,forecast:{id:"p27"}}}))); await settle(); render();
    assert.equal(slots[1].sources.forecast.id,"p26");
    assert.ok(replacements.every(url=>!url.includes("p27")));
    assert.equal(slots[3],false,"returning to a retained snapshot ends the cancelled read indicator");
  } finally {
    cleanups.forEach(fn=>fn?.());
    for (const [key,value] of Object.entries(original)) if(value===undefined) delete globalThis[key]; else globalThis[key]=value;
  }
});
