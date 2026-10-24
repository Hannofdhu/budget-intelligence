import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ActionTable } from "./ActionTable";
import { Xiaoyu } from "./Xiaoyu";
import type { ChatMessage } from "./aiChatApi";

export function chatSafeUrl(value: string): string {
  if (/^https:\/\//i.test(value)) return value;
  if (/^\/budget\/[a-z-]+(?:[?#]|$)/.test(value) && !/[\\\u0000-\u001f]/.test(value)) return value;
  return "";
}

export function AiChatMessage({ message, showCompanion = true }: { message: ChatMessage; showCompanion?: boolean }) {
  const [copyNotice, setCopyNotice] = useState("");
  const user = message.role === "user", busy = ["queued", "preparing", "running", "stopping"].includes(message.state);
  const needsReview = message.validation?.state === "needs_review";
  const copy = async () => {
    try { if (!navigator.clipboard?.writeText) throw new Error(); await navigator.clipboard.writeText((needsReview ? `待核实：${message.validation!.warnings.join("；")}\n\n` : "") + message.text); setCopyNotice("已复制"); }
    catch { setCopyNotice("复制失败，请选中文字后使用系统复制"); }
  };
  return <article className={`ai-chat-message ${user ? "is-user" : "is-assistant"}`} aria-label={user ? "我的消息" : "AI智解的回答"}>
    <div className="ai-chat-message-meta"><span>{user ? "我" : "AI智解"}</span>{!user && busy && <span className="ai-chat-progress">{message.progress || ({ queued: "排队中", preparing: "正在准备", stopping: "正在停止" } as Record<string,string>)[message.state] || "正在回答"}</span>}</div>
    {!user && needsReview && <div className="ai-knowledge-answer-warning" role="status"><strong>待核实</strong>{message.validation!.warnings.map(warning => <p key={warning}>{warning}</p>)}<small>暂不提供此回答表格的一键导出；复制回答会带上本提示。</small></div>}
    {message.activities && message.activities.length > 0 && <details className="ai-chat-activities"><summary>执行过程 · {message.activities.length}项{busy ? " · 进行中" : ""}</summary><ol>{message.activities.map((activity, index) => <li key={activity.id || index}>
      <details><summary><span>{({ bash: "运行分析", read: "读取文件", write: "生成文件", edit: "调整工作区文件", grep: "检索资料", ls: "查看工作区", find: "查找文件" } as Record<string, string>)[activity.tool] || "处理分析任务"}</span><small>{({ running: "进行中", complete: "已完成", error: "未完成", interrupted: "已中断" } as Record<string, string>)[activity.state] || activity.state}</small></summary><code>{activity.summary}</code>{activity.output && <pre>{activity.output}</pre>}</details>
    </li>)}</ol></details>}
    {user ? <p className="ai-chat-user-text">{message.text}</p> : message.text ? <div className="ai-chat-prose"><Markdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={chatSafeUrl} components={{
      a: ({ href, children }) => href ? <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> : <span>{children}</span>,
      img: ({ alt }) => <span className="ai-chat-image-note">[图片：{alt || "不自动加载外部图片"}]</span>,
      table: ({ children }) => needsReview ? <div className="ai-chat-table-scroll"><table>{children}</table></div> : <ActionTable keyboardNavigation tableLabel="AI回答表格" exportLabel="导出表格" scrollClassName="ai-chat-table-scroll" scrollLabel="AI回答表格，可横向滚动">{children}</ActionTable>,
    }}>{message.text}</Markdown></div> : busy ? <div className="ai-chat-waiting" role="status">{showCompanion && <Xiaoyu pose={message.state === "queued" ? "focus" : message.state === "stopping" ? "notice" : "think"}/>}<span>{message.state === "queued" ? "有空位后会自动开始，无需再次发送。" : message.state === "stopping" ? "正在停止回答，已有内容会保留。" : "正在处理这条问题"}</span></div> : null}
    {message.error && <p className="ai-chat-message-error" role="status">{message.error}</p>}
    {!user && message.state === "interrupted" && <p className="ai-chat-message-note">回答已停止，以上内容和执行记录已保留。</p>}
    {!user && message.state === "cancelled" && <p className="ai-chat-message-note">已取消排队，未调用模型。问题仍保留，可重新提问。</p>}
    {!user && message.text && !busy && <div className="ai-chat-message-actions"><button onClick={() => { void copy(); }} aria-label="复制这条回答">复制回答</button>{message.model && <span>{message.model}</span>}<span role="status">{copyNotice}</span></div>}
    {!user && message.validation && <details className="ai-knowledge-checks"><summary>口径检查范围{needsReview ? " · 待核实" : " · 有限检查"}</summary><p>{message.validation.notice}</p><p>{message.validation.resources.length ? `本轮读取：${message.validation.resources.join("、")}` : "本轮未记录成功的预算读取。"}</p></details>}
  </article>;
}
