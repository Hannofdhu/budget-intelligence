import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Xiaoyu } from "./Xiaoyu";
import { prepareXiaoyuHelp } from "./xiaoyuHelpHandoff";
import { xiaoyuHelpErrorCopy, xiaoyuHelpTopic, type XiaoyuHelpSnapshot, type XiaoyuHelpTopicId } from "./xiaoyuHelpTopics";
import "./xiaoyuHelp.css";

const openedEvent = "budget:xiaoyu-help-open";
type Context = { source: string; snapshot: XiaoyuHelpSnapshot; versionLabel: string; fiscalYear: number };

/** A global pattern: each caller supplies its real page state; no business action is invoked here. */
export function XiaoyuHelp({ topicId, getContext }: { topicId: XiaoyuHelpTopicId; getContext: () => Context }) {
  const topic = xiaoyuHelpTopic(topicId)!;
  const id = useId(), trigger = useRef<HTMLButtonElement>(null), panel = useRef<HTMLElement>(null), heading = useRef<HTMLHeadingElement>(null);
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState(""), [error, setError] = useState("");
  const close = (restoreFocus = false) => { setOpen(false); if (restoreFocus) trigger.current?.focus({ preventScroll: true }); };
  const prepare = () => {
    const next = getContext(); setError("");
    try { const link = prepareXiaoyuHelp(next.source, topicId, next.snapshot, "explain"); setHref(link); return link; }
    catch { setHref(""); setError(xiaoyuHelpErrorCopy); return ""; }
  };
  const toggle = () => {
    if (open) { close(true); return; }
    window.dispatchEvent(new CustomEvent(openedEvent, { detail: id }));
    prepare(); setOpen(true);
  };
  useEffect(() => {
    const another = (event: Event) => { if ((event as CustomEvent).detail !== id) close(); };
    window.addEventListener(openedEvent, another);
    return () => window.removeEventListener(openedEvent, another);
  }, [id]);
  useLayoutEffect(() => {
    if (!open || !panel.current) return;
    const card = panel.current;
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect(); if (!rect) return;
      if (window.innerWidth <= 600) {
        Object.assign(card.style, { left: "8px", right: "8px", top: "auto", bottom: "8px", width: "auto", maxHeight: "calc(100dvh - 24px)" });
      } else {
        const width = Math.min(480, window.innerWidth - 24);
        Object.assign(card.style, { width: `${width}px`, right: "auto", bottom: "auto", maxHeight: `${window.innerHeight - 32}px` });
        const height = card.getBoundingClientRect().height, above = rect.top - height - 12;
        Object.assign(card.style, { left: `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`,
          top: `${Math.max(12, Math.min(above >= 12 ? above : rect.bottom + 12, window.innerHeight - height - 12))}px` });
      }
    };
    position(); heading.current?.focus({ preventScroll: true });
    window.addEventListener("resize", position); window.addEventListener("scroll", position, true);
    return () => { window.removeEventListener("resize", position); window.removeEventListener("scroll", position, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const node = event.target as Node;
      if (!trigger.current?.contains(node) && !panel.current?.contains(node)) close();
    };
    document.addEventListener("pointerdown", outside); document.addEventListener("focusin", outside);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); };
  }, [open]);

  return <>
    <button ref={trigger} type="button" className="xiaoyu-help-trigger" aria-label={`小予讲解：${topic.title}`}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={toggle}>
      <Xiaoyu pose="focus"/><span>小予讲解</span>
    </button>
    {open && typeof document !== "undefined" && createPortal(
      <section ref={panel} id={id} className="xiaoyu-help-card" role="dialog" aria-labelledby={`${id}-title`}
        onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); } }}>
        <header className="xiaoyu-help-heading"><Xiaoyu pose="hello"/><div><span>小予讲解 · {topic.title}</span><h2 ref={heading} id={`${id}-title`} tabIndex={-1}>{topic.headline}</h2></div>
          <button type="button" className="xiaoyu-help-close" onClick={() => close(true)} aria-label="关闭小予讲解">×</button></header>
        <div className="xiaoyu-help-body">
          {topic.cardSentences.map((sentence, index) => <p className={index === topic.cardSentences.length - 1 ? "is-key" : undefined} key={sentence}>{sentence}</p>)}
          {error && <p role="alert" className="xiaoyu-help-error">{error}<button type="button" onClick={prepare}>重试</button></p>}
        </div>
        <footer className="xiaoyu-help-footer"><a href={href || undefined} aria-disabled={!href || undefined} target="_blank" rel="noopener noreferrer"
          onClick={event => { const fresh = prepare(); if (!fresh) event.preventDefault(); else event.currentTarget.href = fresh; }}>问问 AI智解 <span aria-hidden="true">↗</span></a></footer>
      </section>, document.body)}
  </>;
}
