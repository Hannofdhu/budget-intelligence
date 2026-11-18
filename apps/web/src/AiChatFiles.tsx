import { useEffect, useRef, useState } from "react";
import { chatFileSize, readChatFile, type ChatFile, type ChatFiles } from "./aiChatFilesApi";
import "./aiChatFiles.css";

function FileIcon({ file }: { file: ChatFile }) {
  const spreadsheet = ["Excel", "CSV"].includes(file.type_label), image = file.media_type.startsWith("image/");
  return <span className={`ai-file-icon${spreadsheet ? " is-spreadsheet" : ""}`} aria-hidden="true"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z M14 3v6h6"/>
    <path d={spreadsheet ? "M8 12h8v6H8Z M8 15h8 M12 12v6" : image ? "m7 18 3-4 3 3 2-2 2 3 M9 11h.01" : "M8 13h8 M8 17h6"}/>
  </svg></span>;
}

export function AiChatFiles({ conversationId, collection, busy, loading, listError, onRefresh }: {
  conversationId: string; collection: ChatFiles; busy: boolean; loading: boolean; listError: string; onRefresh: () => void;
}) {
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [working, setWorking] = useState("");
  const [preview, setPreview] = useState<{ id: string; url: string } | null>(null);
  const alive = useRef(true), active = useRef<AbortController | null>(null), operation = useRef(false);
  const urls = useRef(new Set<string>()), timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => { alive.current = true; return () => { alive.current = false; active.current?.abort(); for (const timer of timers.current) clearTimeout(timer); for (const url of urls.current) URL.revokeObjectURL(url); }; }, []);
  useEffect(() => {
    setPreview(current => {
      if (!current || collection.files.some(file => file.id === current.id)) return current;
      URL.revokeObjectURL(current.url); urls.current.delete(current.url); return null;
    });
  }, [collection.files]);

  const closePreview = () => { if (preview) { URL.revokeObjectURL(preview.url); urls.current.delete(preview.url); setPreview(null); } };
  const openFile = async (file: ChatFile, show: boolean) => {
    if (operation.current || busy || collection.writing) return;
    if (show && preview?.id === file.id) { closePreview(); return; }
    operation.current = true; setWorking(`${file.id}:${show ? "preview" : "download"}`); setError(""); setNotice("");
    const controller = new AbortController(); active.current = controller;
    const timer = setTimeout(() => controller.abort(), 60_000);
    try {
      const blob = await readChatFile(conversationId, file, show, controller.signal);
      if (!alive.current || controller.signal.aborted) return;
      const url = URL.createObjectURL(blob); urls.current.add(url);
      if (show) { closePreview(); setPreview({ id: file.id, url }); }
      else {
        const anchor = document.createElement("a"); anchor.href = url; anchor.download = file.name; document.body.append(anchor); anchor.click(); anchor.remove();
        const release = setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); timers.current.delete(release); }, 60_000); timers.current.add(release);
        setNotice(`“${file.name}”已交给浏览器下载`);
      }
    } catch (failure) { if (alive.current) setError(controller.signal.aborted ? "文件读取超时，请重试" : failure instanceof Error ? failure.message : "文件读取失败，请刷新重试"); }
    finally { clearTimeout(timer); operation.current = false; active.current = null; if (alive.current) setWorking(""); }
  };
  if (!collection.files.length && !listError && !collection.truncated) return null;
  const writing = busy || collection.writing;
  return <section className="ai-chat-files" aria-label="本条回答生成的文件" aria-busy={loading || Boolean(working)}>
    <header><h2>本条回答生成的文件{collection.files.length > 0 && <span>{collection.files.length}个</span>}</h2><button type="button" disabled={loading || busy} onClick={() => { setError(""); onRefresh(); }}>{loading ? "正在读取…" : "刷新"}</button></header>
    {collection.files.length > 0 && <ul>{collection.files.map(file => <li key={file.id} className="ai-file-card">
      <div className="ai-file-row"><FileIcon file={file}/><div className="ai-file-description"><strong title={file.name}>{file.name}</strong><span>{file.type_label} · {chatFileSize(file.size)}{file.notice ? ` · ${file.notice}` : ""}</span></div>
        <div className="ai-file-actions">{file.previewable && <button type="button" aria-label={`预览 ${file.name}`} aria-expanded={preview?.id === file.id} disabled={writing || Boolean(working)} onClick={() => { void openFile(file, true); }}>{working === `${file.id}:preview` ? "正在读取…" : preview?.id === file.id ? "收起" : "预览"}</button>}
          <button type="button" aria-label={`下载 ${file.name}`} disabled={writing || Boolean(working) || !file.downloadable} onClick={() => { void openFile(file, false); }}>{working === `${file.id}:download` ? "准备中…" : "下载"}</button></div></div>
      {preview?.id === file.id && <div className="ai-file-preview"><img src={preview.url} alt={file.name} onError={() => { closePreview(); setError("图片无法预览，可下载后查看"); }}/></div>}
    </li>)}</ul>}
    {error && <p className="ai-file-error" role="alert">{error}</p>}
    {listError && <p className="ai-file-error" role="alert">{listError}</p>}
    {notice && <p className="ai-file-notice" role="status">{notice}</p>}
    {collection.truncated && <p className="ai-file-notice">文件较多，当前仅展示扫描范围内的前200个支持文件；每个文件下载上限32MB。</p>}
    <p className="ai-file-note">{writing ? "本轮正在生成文件，完成后刷新即可下载。" : "文件来自本对话，AI生成内容请核对后使用。"}</p>
  </section>;
}
