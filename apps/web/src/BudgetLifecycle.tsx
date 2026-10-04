import { useState } from "react";

export function BudgetLifecycle({ mode, onView, onContinue, onStart, busy = false }: {
  mode: "draft" | "published" | "historical";
  onView?: () => void;
  onContinue?: () => void;
  onStart?: (reason: string) => Promise<void>;
  busy?: boolean;
}) {
  const [creating, setCreating] = useState(false);
  const [reason, setReason] = useState("");
  const readonly = mode !== "draft";
  return <section className="budget-lifecycle" aria-label="预算状态">
    <div><strong>{readonly ? "查看预算" : "编制预算"}</strong><span>{readonly ? "已定版 · 只读" : "当前草稿"}</span></div>
    {onView && <button disabled={busy} onClick={onView}>查看定版预算</button>}
    {onContinue && <button disabled={busy} onClick={onContinue}>继续修订草稿</button>}
    {onStart && !creating && <button disabled={busy} onClick={() => setCreating(true)}>开始修订</button>}
    {creating && onStart && <form className="budget-revision-start" onSubmit={event => { event.preventDefault(); if (reason.trim() && !busy) void onStart(reason.trim()); }}>

      <label>修订原因<input autoFocus required disabled={busy} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <div><button disabled={busy || !reason.trim()} type="submit">创建修订草稿</button><button disabled={busy} type="button" onClick={() => setCreating(false)}>取消</button></div>
    </form>}
  </section>;
}
