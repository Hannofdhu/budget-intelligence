import fs from 'node:fs';

const file = 'index.html';
let html = fs.readFileSync(file, 'utf8');
const slides = fs.readFileSync('slides-fragment.html', 'utf8').trim();
const notes = fs.readFileSync('notes-fragment.js', 'utf8').trim();
const css = fs.readFileSync('deck-overrides.css', 'utf8');

html = html.replace('<title>[\u5fc5\u586b] \u66ff\u6362\u4e3a PPT \u6807\u9898 \u00b7 Deck Title</title>', '<title>\u9884\u7b97\u667a\u89e3 \u00b7 \u5f00\u9898\u62a5\u544a</title>');
html = html.replace('</style>\n</head>', `${css}</style>\n</head>`);

const slideStart = html.indexOf('<!-- ============ \u793a\u4f8b:\u7b2c 1 \u9875');
const deckEnd = html.indexOf('\n</div>\n\n<div id="nav"></div>', slideStart);
if (slideStart < 0 || deckEnd < 0) throw new Error('Cannot find slide insertion range');
html = `${html.slice(0, slideStart)}${slides}\n${html.slice(deckEnd)}`;

const notesStart = html.indexOf('const SPEAKER_NOTES = [');
const notesEndMarker = 'window.__SPEAKER_NOTES__ = SPEAKER_NOTES;';
const notesEnd = html.indexOf(notesEndMarker, notesStart) + notesEndMarker.length;
if (notesStart < 0 || notesEnd < notesEndMarker.length) throw new Error('Cannot find speaker notes range');
html = `${html.slice(0, notesStart)}${notes}${html.slice(notesEnd)}`;

fs.writeFileSync(file, html);
console.log(file);
