import cloudbase from "@cloudbase/js-sdk";
import { type ReactElement, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

declare global {
  interface ImportMetaEnv {
    readonly VITE_CLOUDBASE_ENV_ID?: string;
    readonly VITE_CLOUDBASE_PUBLISHABLE_KEY?: string;
    readonly VITE_CLOUDBASE_E2_PROVIDER?: string;
  }

  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }
}

const envId = String(import.meta.env.VITE_CLOUDBASE_ENV_ID ?? "").trim();
const publishableKey = String(import.meta.env.VITE_CLOUDBASE_PUBLISHABLE_KEY ?? "").trim();
const providerId = String(import.meta.env.VITE_CLOUDBASE_E2_PROVIDER ?? "sretxbase").trim();
const authEnabled = Boolean(envId && publishableKey && providerId);

const cloudbaseApp = authEnabled
  ? cloudbase.init({
      env: envId,
      region: "ap-shanghai",
      accessKey: publishableKey,
      auth: { detectSessionInUrl: true },
    })
  : null;

const auth = cloudbaseApp?.auth ?? null;

let fetchInterceptorInstalled = false;

function installAuthenticatedFetch(): void {
  if (!auth || fetchInterceptorInstalled) return;
  fetchInterceptorInstalled = true;
  const nativeFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const rawUrl = input instanceof Request ? input.url : String(input);
    const url = new URL(rawUrl, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/")) {
      return nativeFetch(input, init);
    }

    const { data } = await auth.getSession();
    const token = data?.session?.access_token;
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
    return nativeFetch(input, { ...init, headers });
  };
}

type GateState = "checking" | "signed-out" | "signed-in" | "error";

function LoginScreen({ error, onLogin }: { error: string; onLogin: () => void }): ReactElement {
  return (
    <main className="e2-login-shell">
      <section className="e2-login-card" aria-labelledby="e2-login-title">
        <div className="e2-login-mark">预算智解</div>
        <p className="e2-login-eyebrow">公司创新大赛 · 脱敏评审环境</p>
        <h1 id="e2-login-title">使用新东方 E2 账号进入</h1>
        <p className="e2-login-copy">本环境部署的是当前本地最新版，内含丰富生成数据；登录后可实际操作预算、预测、明年预算与版本审计功能。</p>
        <button className="e2-login-button" type="button" onClick={onLogin}>E2 登录</button>
        {error && <p className="e2-login-error" role="alert">{error}</p>}
        <p className="e2-login-note">仅限新东方内部评审使用，不连接真实业务数据。</p>
      </section>
    </main>
  );
}

export function E2AuthGate({ children }: { children: ReactNode }): ReactElement {
  const [state, setState] = useState<GateState>(authEnabled ? "checking" : "signed-in");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!auth) return;
    let active = true;
    installAuthenticatedFetch();
    void auth.getSession().then(({ data, error: sessionError }) => {
      if (!active) return;
      if (sessionError) {
        setError(sessionError.message || "E2 会话校验失败，请重试。");
        setState("error");
        return;
      }
      setState(data?.session ? "signed-in" : "signed-out");
    });
    return () => { active = false; };
  }, []);

  const login = useCallback(() => {
    if (!auth) return;
    setError("");
    setState("checking");
    const redirectTo = `${window.location.origin}${window.location.pathname}`;
    void auth.signInWithOAuth({ provider: providerId, options: { redirectTo } }).then(({ data, error: loginError }) => {
      if (loginError || !data?.url) {
        setError(loginError?.message || "未能获取 E2 登录地址，请稍后重试。");
        setState("error");
        return;
      }
      window.location.assign(data.url);
    });
  }, []);

  const logout = useCallback(() => {
    if (!auth) return;
    void auth.signOut().finally(() => {
      setState("signed-out");
      window.history.replaceState({}, "", window.location.pathname);
    });
  }, []);

  const loginError = state === "error" ? error : "";
  const content = useMemo(() => {
    if (state === "checking") {
      return <main className="e2-login-shell"><p className="e2-login-loading" role="status">正在校验 E2 登录状态…</p></main>;
    }
    if (state === "signed-out" || state === "error") return <LoginScreen error={loginError} onLogin={login} />;
    return (
      <>
        {children}
        {authEnabled && <button className="e2-session-badge" type="button" onClick={logout} title="退出 E2 登录">E2 已登录 · 退出</button>}
      </>
    );
  }, [children, login, loginError, logout, state]);

  return <>{content}</>;
}

