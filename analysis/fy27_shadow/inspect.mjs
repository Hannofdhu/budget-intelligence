import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const filename = process.argv[2];
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(filename));
console.log((await workbook.inspect({kind:'sheet', include:'id,name', maxChars:4000})).ndjson);
for (const name of ['滚动表', '参数表']) {
  const sheet = workbook.worksheets.getItem(name);
  console.log(JSON.stringify({sheet:name, headers:sheet.getRange('A1:Q2').values}));
  console.log((await workbook.inspect({kind:'formula', sheetId:name, range:'A6:CX8', maxChars:1800, options:{maxResults:12}})).ndjson);
}
const preview = await workbook.render({sheetName:'滚动表',range:'A1:Q12',scale:1,format:'png'});
await fs.mkdir('output/fy27-shadow',{recursive:true});
await fs.writeFile('output/fy27-shadow/source-preview.png',new Uint8Array(await preview.arrayBuffer()));
