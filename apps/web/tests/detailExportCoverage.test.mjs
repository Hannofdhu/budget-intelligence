import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";

test("owned budget tables require explicit detail projections rather than rendered-text export fallbacks", () => {
  const directory = fileURLToPath(new URL("../src/", import.meta.url));
  const missing = []; let owners = 0, paged = 0;
  for (const filename of fs.readdirSync(directory).filter(name => name.endsWith(".tsx"))) {
    const file = ts.createSourceFile(filename, fs.readFileSync(`${directory}/${filename}`, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = node => {
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const name = node.tagName.getText(file), attrs = new Set(node.attributes.properties.filter(ts.isJsxAttribute).map(attr => attr.name.getText(file)));
        // AI-authored explanatory Markdown tables have no original budget record schema.
        if (name === "ActionTable" && filename !== "AiChatMessage.tsx") {
          owners++; if (!attrs.has("exportData")) missing.push(`${filename}:${file.getLineAndCharacterOfPosition(node.pos).line + 1} ActionTable`);
        }
        if (name === "PagedTable") {
          paged++; if (!attrs.has("exportColumns") || !attrs.has("exportRow")) missing.push(`${filename}:${file.getLineAndCharacterOfPosition(node.pos).line + 1} PagedTable`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  assert.ok(owners >= 23 && paged >= 20, "scan every current owner, including reusable paged details");
  assert.deepEqual(missing, [], "front-end layout is not an analytical export schema");
});
