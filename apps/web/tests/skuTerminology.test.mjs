import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { skuText } from "../src/budgetDisplay.ts";

const source = file => fs.readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");

test("SKU terminology changes display text without rewriting historical messages or values", () => {
  const saved = { message: "预算业务范围或来源已变化，不能静默替换", amount: "0.000001", id: "combination-1" };
  const before = JSON.stringify(saved);
  assert.equal(skuText(saved.message), "预算SKU或来源已变化，不能静默替换");
  assert.equal(skuText("当前业务范围 · 业务范围"), "当前SKU · SKU");
  assert.equal(skuText("  SKU / 校区 / 产品 / 年级 / 科目 · 0 元  "), "  SKU / 校区 / 产品 / 年级 / 科目 · 0 元  ");
  assert.equal(JSON.stringify(saved), before);
});

test("shipped frontend labels have no old business-scope wording except explicit compatibility readers", () => {
  const directory = new URL("../src/", import.meta.url);
  for (const file of fs.readdirSync(directory).filter(file => /\.(?:ts|tsx)$/.test(file))) {
    const display = source(file)
      .replace('value.replaceAll("业务范围", "SKU")', "display-only alias")
      .replace('/业务范围|SKU|组合|COMBINATION/i', "legacy error matcher");
    assert.doesNotMatch(display, /业务范围/, file);
  }
  const next = source("NextYearBudgetPage.tsx");
  assert.match(next, /label = "当前SKU"/);
  assert.match(next, /section=\{\{ id: "next-year-sku", title: "SKU"/,
    "the shared section owns the peer-level SKU heading rather than a module-specific h3");
  assert.match(next, /label=\{`\$\{fy\(fiscalYear\)\}SKU`\}/);
  assert.match(source("WorkspacePage.tsx"), /\/业务范围\|SKU\|组合\|COMBINATION\/i/,
    "legacy and new errors keep the same repair destination");
});

test("old server diagnostic text is projected for display and explicit exports, not rewritten in API inputs", () => {
  const next = source("NextYearBudgetPage.tsx");
  assert.match(next, /proof: skuText\(row\.bound_proof\?\.message \?\? "未检查"\)/);
  assert.match(next, /reason: skuText\(action\.reason\)/);
  assert.match(next, /<td>\{skuText\(issue\.message\)\}<\/td>/);
  assert.match(source("ForecastDataReview.tsx"), /skuText\(issue\.message\)/);
  assert.doesNotMatch(source("nextYearApi.ts"), /skuText|replaceAll\("业务范围"/,
    "the read/write API and frozen payloads do not perform terminology migration");
});
