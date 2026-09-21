import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const issues = [];

function listFiles(dir, predicate = () => true) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return listFiles(full, predicate);
    return predicate(full) ? [full] : [];
  });
}

const acceptancePath = path.join(root, "docs/product/phase-1-acceptance-cases.md");
const acceptance = fs.readFileSync(acceptancePath, "utf8");
const acceptanceIds = [...acceptance.matchAll(/^### (AB-\d+[A-Z]?)\b/gm)].map((match) => match[1]);
const duplicateAcceptanceIds = acceptanceIds.filter((id, index) => acceptanceIds.indexOf(id) !== index);
if (acceptanceIds.length !== 96) issues.push(`acceptance count: expected 96, got ${acceptanceIds.length}`);
if (duplicateAcceptanceIds.length) issues.push(`duplicate acceptance IDs: ${[...new Set(duplicateAcceptanceIds)].join(", ")}`);

const traceabilityPath = path.join(root, "tests/acceptance/traceability.yaml");
let tracedAcceptanceIds = [];
if (!fs.existsSync(traceabilityPath)) {
  issues.push("missing machine-readable acceptance trace: tests/acceptance/traceability.yaml");
} else {
  try {
    // JSON is a strict YAML subset; keeping this file JSON-shaped avoids a runtime YAML dependency.
    const traceability = JSON.parse(fs.readFileSync(traceabilityPath, "utf8"));
    const cases = traceability.acceptance_cases ?? [];
    const closureCapabilities = traceability.closure_capabilities ?? [];
    tracedAcceptanceIds = cases.map((item) => item.id);
    const expected = Array.from({ length: 12 }, (_, index) => `AB-${String(index + 50).padStart(3, "0")}`);
    if (JSON.stringify(tracedAcceptanceIds) !== JSON.stringify(expected)) {
      issues.push(`acceptance trace IDs: expected ${expected.join(", ")}, got ${tracedAcceptanceIds.join(", ")}`);
    }
    const traceRows = [...cases, ...closureCapabilities];
    for (const row of traceRows) {
      if (row.status !== undefined) issues.push(`acceptance trace ${row.id}: execution status belongs in generated reports, not the binding registry`);
      if (!Array.isArray(row.automated_tests) || row.automated_tests.length === 0) {
        issues.push(`acceptance trace ${row.id}: at least one automated test is required`);
        continue;
      }
      if (row.spec_anchor) {
        const [specFile] = row.spec_anchor.split("#", 1);
        if (!fs.existsSync(path.join(root, specFile)) || !acceptance.includes(`### ${row.id}`)) {
          issues.push(`acceptance trace ${row.id}: invalid specification anchor`);
        }
      }
      if (row.ui_evidence && !fs.existsSync(path.join(root, row.ui_evidence))) {
        issues.push(`acceptance trace ${row.id}: missing UI evidence ${row.ui_evidence}`);
      }
      for (const nodeId of row.automated_tests) {
        const [testFile, testName] = nodeId.split("::");
        const fullTestPath = path.join(root, testFile);
        if (!testName || !fs.existsSync(fullTestPath)) {
          issues.push(`acceptance trace ${row.id}: invalid pytest node ${nodeId}`);
          continue;
        }
        const testSource = fs.readFileSync(fullTestPath, "utf8");
        if (!testSource.includes(`def ${testName}(`)) {
          issues.push(`acceptance trace ${row.id}: pytest function not found ${nodeId}`);
        }
      }
    }
  } catch (error) {
    issues.push(`invalid acceptance trace JSON/YAML subset: ${error.message}`);
  }
}

const adrDir = path.join(root, "docs/adr");
const adrFiles = fs.readdirSync(adrDir).filter((name) => /^\d{4}-.*\.md$/.test(name)).sort();
const adrNumbers = adrFiles.map((name) => Number(name.slice(0, 4)));
if (adrFiles.length !== 151) issues.push(`ADR count: expected 151, got ${adrFiles.length}`);
for (let number = 1; number <= 151; number += 1) {
  const matches = adrNumbers.filter((value) => value === number).length;
  if (matches !== 1) issues.push(`ADR number ${String(number).padStart(4, "0")}: expected once, got ${matches}`);
}

const expectedTechnicalDocs = [
  "README.md",
  "phase-1-architecture.md",
  "phase-1-data-model.md",
  "phase-1-calculation-engine.md",
  "phase-1-api-state-security.md",
  "phase-1-test-delivery.md",
];
for (const name of expectedTechnicalDocs) {
  if (!fs.existsSync(path.join(root, "docs/technical", name))) issues.push(`missing technical document: ${name}`);
}

const markdownFiles = [path.join(root, "CONTEXT.md"), ...listFiles(path.join(root, "docs"), (file) => file.endsWith(".md"))];
for (const file of markdownFiles) {
  const content = fs.readFileSync(file, "utf8");
  for (const match of content.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    const target = match[1].trim();
    if (/^(https?:|mailto:|#)/.test(target)) continue;
    const decoded = decodeURIComponent(target.split("#", 1)[0]);
    const resolved = path.resolve(path.dirname(file), decoded);
    if (!fs.existsSync(resolved)) issues.push(`broken link: ${path.relative(root, file)} -> ${target}`);
  }
}

const fieldCatalog = fs.readFileSync(path.join(root, "docs/product/phase-1-field-catalog.md"), "utf8");
if (fieldCatalog.includes("待技术设计确定但不改变业务口径的事项")) {
  issues.push("field catalog still contains unresolved technical-design section");
}

console.log(JSON.stringify({
  acceptance_count: acceptanceIds.length,
  acceptance_first: acceptanceIds[0],
  acceptance_last: acceptanceIds.at(-1),
  traced_acceptance_count: tracedAcceptanceIds.length,
  adr_count: adrFiles.length,
  adr_first: adrFiles[0],
  adr_last: adrFiles.at(-1),
  markdown_files_checked: markdownFiles.length,
  issues,
}, null, 2));

if (issues.length) process.exitCode = 1;
