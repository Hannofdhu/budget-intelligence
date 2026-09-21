import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = "/Users/hann/Documents/预算智解/【智慧学习部】FY27预算拆解（1.13亿）-备份.xlsx";
const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const sheets = await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 10000,
});
console.log("SHEETS");
console.log(sheets.ndjson);

const matches = await workbook.inspect({
  kind: "match",
  searchTerm: "1\\.06|税|结转|新签|联报|扩科|召回",
  options: { useRegex: true, maxResults: 300 },
  maxChars: 30000,
});
console.log("MATCHES");
console.log(matches.ndjson);

for (const [sheetName, rangeAddress] of [
  ["滚动表", "AS1:CX8"],
  ["参数表", "AS1:BQ12"],
  ["新扩召拆解", "A1:AP8"],
  ["新签拆解", "A1:Y8"],
]) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const range = sheet.getRange(rangeAddress);
  console.log(`RANGE ${sheetName}!${rangeAddress}`);
  console.log(JSON.stringify({ values: range.values, formulas: range.formulas }));
}
