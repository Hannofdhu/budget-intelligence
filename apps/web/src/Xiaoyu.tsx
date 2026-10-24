import "./xiaoyu.css";

type XiaoyuPose = "hello" | "think" | "focus" | "done" | "notice" | "rest";

/** Decorative companion: the adjacent text always states the actual product status. */
export function Xiaoyu({ pose, size = "inline" }: { pose: XiaoyuPose; size?: "welcome" | "inline" | "empty" }) {
  return <img className={`xiaoyu is-${size}`} src={`/mascot/xiaoyu-${pose}.svg`}
    width="360" height="340" alt="" aria-hidden="true" draggable="false" />;
}
