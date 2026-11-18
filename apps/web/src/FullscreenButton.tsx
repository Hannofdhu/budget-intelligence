import { useEffect, useRef, useState } from "react";

export function FullscreenButton() {
  const [fullscreen, setFullscreen] = useState(() => typeof document !== "undefined" && Boolean(document.fullscreenElement));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const sync = () => { setFullscreen(Boolean(document.fullscreenElement)); setError(""); };
    sync();
    document.addEventListener("fullscreenchange", sync);
    return () => {
      mounted.current = false;
      document.removeEventListener("fullscreenchange", sync);
    };
  }, []);

  const toggle = async () => {
    if (pending.current) return;
    if (!document.fullscreenElement && (!document.documentElement.requestFullscreen || document.fullscreenEnabled === false)) {
      setError("当前浏览器不支持网页全屏，请使用浏览器自带的全屏功能。");
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      // Keep the whole product in place: no navigation, remount, zoom or data operation.
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      if (mounted.current) setError("未能切换全屏，请重试，或使用浏览器自带的全屏功能。");
    } finally {
      pending.current = false;
      if (mounted.current) {
        setFullscreen(Boolean(document.fullscreenElement));
        setBusy(false);
      }
    }
  };

  return <div className="budget-fullscreen">
    <button type="button" className="budget-fullscreen-button" aria-label={fullscreen ? "退出产品全屏" : "全屏显示产品"}
      aria-pressed={fullscreen} aria-busy={busy} disabled={busy}
      title={fullscreen ? "退出全屏（也可按 Esc）" : "全屏显示产品"} onClick={() => void toggle()}>
      <svg aria-hidden="true" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d={fullscreen ? "M7 2v5H2m11-5v5h5M2 13h5v5m6 0v-5h5" : "M7 2H2v5m11-5h5v5M2 13v5h5m6 0h5v-5"} />
      </svg>
      <span>{fullscreen ? "退出全屏" : "全屏"}</span>
    </button>
    {error && <span className="budget-fullscreen-error" role="alert">{error}</span>}
  </div>;
}
