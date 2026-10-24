export type XiaoyuHelpTopic = {
  module: "budget" | "forecast" | "next-year";
  title: string; revision: string; headline: string;
  cardSentences: readonly string[];
  sections: readonly { title: string; text: string }[];
  questions: readonly { id: string; label: string; prompt: string }[];
};

/** Reviewed explanations, shared by the card and AI. Add new topics here, then bind a trigger. */
export const xiaoyuHelpTopics = {
  "next-year.impact-preview": {
    module: "next-year", title: "查看影响", revision: "2026-10-24.1", headline: "先看变化，再决定保存。",
    cardSentences: [
      "用这份预算的参数和本页修改，先算一遍。",
      "对比新结果和已保存结果，看看收入、人次和班量怎么变。",
      "这里只预览，不会保存。满意后，再点“保存新版本”。",
    ],
    sections: [
      { title: "拿什么来算？", text: "用这份预算已有的参数，加上你在本页填写的修改。编制基准仍是这份预算引用的全年预测，不会自动换成最新预测。" },
      { title: "点下去会怎样？", text: "先检查输入，再重新计算，最后把预览结果和原来保存的结果放在一起比较。计算覆盖整份预算；页面筛选只影响你查看哪些业务。" },
      { title: "会替我保存吗？", text: "不会。这里只生成未保存预览。确认调整合适后，还要点“保存新版本”，旧版本也会保留。如果只是提高经营目标，没有改招生或收入参数，收入不一定变化。" },
    ],
    questions: [
      { id: "explain", label: "讲讲这个按钮", prompt: "请用第一次接触预算的人也能听懂的话，解释“查看影响”用什么数据、如何计算、点击后会改变什么，以及它与“保存新版本”的区别。" },
      { id: "target", label: "为什么目标改了，收入没变？", prompt: "为什么修改经营目标后，点击“查看影响”，收入可能没有变化？请结合带入的页面状态和修改摘要解释；没有试算结果时，不要猜测变化数值。" },
    ],
  },
  "next-year.track-target-allocation": {
    module: "next-year", title: "分配赛道目标", revision: "2026-10-25.2", headline: "按预计收入，帮你分目标。",
    cardSentences: [
      "参考上次保存的预算，看各赛道预计能收入多少。",
      "比如收入占70%和30%，100万元目标就分70万和30万。",
      "会改写赛道目标，不会增加收入，也不会自动保存。",
    ],
    sections: [
      { title: "怎么讲才容易懂？", text: "先用两三句说明按钮做什么，再举一个标明假设的小例子。用预计收入、分目标这些日常说法。不要用本版本已保存的测算、权重、尾差等术语开场，不要一开始堆版本号、标识和日期；用户追问来源时再解释具体版本。示例数字不是这份预算的真实金额。" },
      { title: "收入从哪里来？", text: "权重来自当前打开的预算已保存版本，各赛道全年预算测算收入的汇总，不是已实现收入，也不是直接拿上一财年全年预测的收入占比。预算参数最初由其绑定的已确认全年预测准备，再经本预算参数测算；不会自动采用最新预测。" },
      { title: "按什么分？", text: "用全年经营目标输入框中的值（包括尚未保存的目标修改），乘以各赛道在本版本已保存测算收入中的占比。收入缺失、无效或为负时按0作为分配权重，全部为0则按赛道数均分。覆盖全部赛道，不受页面筛选影响；新预览、未保存招生或学费参数以及手工填写的赛道目标都不是分配权重。" },
      { title: "为什么分配合计能对上？", text: "先把全年目标四舍五入到分，再按权重分配到分，剩余分按小数余数从大到小补齐；余数相同时按赛道名称排序。合计等于取到分后的全年目标。" },
      { title: "点了会改变什么？", text: "只覆盖本页各赛道目标输入值，保留其他参数修改，并清除已有的未保存预览，需要重新查看影响。不会调整招生、学费或测算收入，不会自动调平，不会保存；正式保留仍需点击保存新版本。" },
    ],
    questions: [
      { id: "explain", label: "讲讲这个按钮", prompt: "“分配赛道目标”是怎么分的？请先说清楚参考哪份预算的预计收入，再用一个简单例子说明。点了会改哪些数字？会不会让收入增加、自动保存？" },
    ],
  },
} as const satisfies Record<string, XiaoyuHelpTopic>;

export type XiaoyuHelpTopicId = keyof typeof xiaoyuHelpTopics;
export const XIAOYU_HELP_CARD_LIMIT = 150;
export const xiaoyuHelpErrorCopy = "AI暂时无法打开，请允许本机存储后重试。";

/** Hard cap includes every visible label, punctuation, and the optional error/retry copy. */
export function xiaoyuHelpCardLength(topic: XiaoyuHelpTopic, withError = false): number {
  return [...[`小予讲解 · ${topic.title}`, topic.headline, ...topic.cardSentences, "×", "问问 AI智解 ", "↗",
    ...(withError ? [xiaoyuHelpErrorCopy, "重试"] : [])].join("")].length;
}

export function validateXiaoyuHelpCard(topic: XiaoyuHelpTopic): void {
  if (xiaoyuHelpCardLength(topic, true) > XIAOYU_HELP_CARD_LIMIT) throw new RangeError(`小予讲解卡片最多${XIAOYU_HELP_CARD_LIMIT}字：${topic.title}`);
}
Object.values(xiaoyuHelpTopics).forEach(validateXiaoyuHelpCard);

export function xiaoyuHelpTopic(id: unknown): XiaoyuHelpTopic | null {
  return typeof id === "string" && Object.hasOwn(xiaoyuHelpTopics, id) ? xiaoyuHelpTopics[id as XiaoyuHelpTopicId] : null;
}

export type XiaoyuHelpSnapshot = {
  state: "saved" | "edited" | "preview";
  changeCount: number;
  changes: { label: string; before: string; after: string }[];
  pendingTools: boolean; validationErrors: boolean; classRuleUpgrade: boolean; operationBusy: boolean;
  basisLabel: string; basisAsOf: string;
  trackTargetAllocation?: {
    annualTargetYuan: string; weightVersionId: string; weightVersionLabel: string;
    weights: { track: string; revenueYuan: string }[];
  };
};

export function xiaoyuHelpState(snapshot: XiaoyuHelpSnapshot): string {
  const state = snapshot.state === "preview" ? "已有未保存预览" : snapshot.state === "edited" ? "参数已修改，结果待重算" : "正在查看已保存结果";
  return [state, `参数改动 ${snapshot.changeCount} 项`, snapshot.pendingTools ? "还有表格或新业务待暂存，需先处理" : "",
    snapshot.validationErrors ? "存在输入错误，需先校验" : "", snapshot.classRuleUpgrade ? "班量口径待按班均更新" : "",
    snapshot.operationBusy ? "当前操作尚未结束" : ""].filter(Boolean).join("；");
}
