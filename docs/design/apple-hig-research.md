# 苹果设计规范研究与预算智解适配依据

检索日期：2026-10-14。适用规范：[预算智解统一设计规范](../../DESIGN.md)。

结论：借鉴苹果的人机界面原则与组件一致性，不复制苹果官网营销页面，不把专业预算产品改成原生 macOS 外观。优先解决同级区块字号和外壳不一致、操作分散、当前位置不清、失败与版本状态难理解等问题。

## 1 来源和采用方式

以下仅使用苹果和 W3C 一手资料。苹果部分页面在普通网页提取中只返回 JavaScript 提示；Typography、Accessibility、Layout 已在真实浏览器读取正文，其余结合官方检索结果核对。没有把第三方 HIG 镜像或二手文章当成规范依据。下表的产品约定是本项目推导，不是官方逐字要求。

| 来源 | 核对的官方要点 | 产品适配 |
| --- | --- | --- |
| [Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles/) | 一致性、清晰层级、自主控制、恢复能力；简洁不等于极简 | 内容优先，复用组件，不隐藏关键数据和错误 |
| [Layout](https://developer.apple.com/design/human-interface-guidelines/layout) | 对齐、分组、渐进披露；适配窗口和文字变化 | 统一区块骨架，按可用空间换行，测量固定导航高度 |
| [Typography](https://developer.apple.com/design/human-interface-guidelines/typography) | 用字号、字重和颜色表达层级；减少字体混用，保持放大后的层级 | 使用语义角色和系统字体回退，而不是页面自行指定 h2/h3 尺寸 |
| [Color](https://developer.apple.com/design/human-interface-guidelines/color) | 颜色语义一致，系统颜色按用途定义 | 保留绿色品牌，增加语义色角色，实际组合验证对比度 |
| [Materials](https://developer.apple.com/design/human-interface-guidelines/materials) | 材质区分功能与内容层，Liquid Glass 不用于内容层 | 预算表不加玻璃或透明叠层；本轮也不要求新增导航玻璃效果 |
| [Lists and tables](https://developer.apple.com/design/human-interface-guidelines/lists-and-tables) | 复杂生产力数据可用多列；清晰列名、合适行样式、辨认内容 | 保留季度横排、识别列与合计，长名称不缩成不可读文字 |
| [Charts](https://developer.apple.com/design/human-interface-guidelines/charts) | 数据突出，描述与坐标辅助，图表同样需要可达 | 口径、范围、单位和图例明确，可从已有透视或明细读取同范围数据 |
| [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars) | 操作分组、位置一致、少而明确的主动作；区别于区域导航 | 模块切换不混入保存，复制与导出就近集中，提交只有一个权威入口 |
| [Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars) | 导航顶级区域，保留区域内状态，当前区域明确 | 四模块固定位置，切换恢复上下文；不为缺少的功能加空标签 |
| [Entering data](https://developer.apple.com/design/human-interface-guidelines/entering-data) | 减少重复输入，说明需要的数据 | 复用已有基准和参数，但经营目标仍按业务约定由用户填写 |
| [Text fields](https://developer.apple.com/design/human-interface-guidelines/text-fields) | 字段目的明确，placeholder 不能代替持续标签 | 单元格编辑有完整可访问名称，错误与字段关联 |
| [Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback) | 操作结果就近反馈，打断程度匹配风险 | 复制不弹模态；丢失草稿等风险保留对话框；失败保留输入 |
| [Progress indicators](https://developer.apple.com/design/human-interface-guidelines/progress-indicators) | 真实反映进度、位置一致、卡住时说明处理方式 | 有真实进度才显示百分比，不用假进度遮掩失败 |
| [Disclosure controls](https://developer.apple.com/design/human-interface-guidelines/disclosure-controls) | 重要操作先呈现，详细功能按需展开，展开入口就近 | 规则和审计折叠，阻断与当前版本不能藏入技术详情 |
| [Writing](https://developer.apple.com/design/human-interface-guidelines/writing) | 清晰行动导向，一致语言，字段错误给出改正方式 | 统一 SKU、结转收入及动作词，错误给出可执行下一步 |
| [Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) | 文字放大、对比度、非纯颜色提示、足够目标、键盘可达 | 用 Web 规则验收，区分细指针和触控环境，保留语义与焦点 |
| [W3C Contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) | 普通文字 4.5:1，大字 3:1，按规范大字定义判断 | 不把 18 CSS px 当成无条件大字；实测每组状态颜色 |
| [W3C Non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) | 必要控件、状态和图形信息对相邻背景有对比要求 | 必要边界和焦点至少 3:1，不要求纯装饰细线全部达到该值 |
| [W3C Focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html) | 键盘焦点不能被作者生成内容完全遮盖 | 本产品进一步要求固定导航和提交栏不遮挡聚焦单元格及错误 |
| [W3C Target size minimum](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) | AA 的指针目标基准为 24×24 CSS px，含规定例外 | 桌面验证目标和间距，产品另取 44px 触控目标，不冒充 Apple 最小值 |
| [W3C Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) | 非二维内容应重排，必要二维内容有例外 | 正文和操作重排；表格局部横滚，不能据此允许整页溢出 |
| [W3C Grid pattern](https://www.w3.org/WAI/ARIA/apg/patterns/grid/) | 交互 grid 需要配套焦点和键盘模型 | 保留原生 table，不能仅套 role 就声称无障碍完成 |

## 2 必须说明的取舍

1. 苹果 HIG 是面向其平台的设计指南，不是 CSS 规范。原生字号与目标尺寸使用 pt，预算智解的尺寸使用 CSS px 或 rem。不能简单一比一映射。
2. 当前苹果无障碍页面列出的 iOS/iPadOS 控件默认尺寸为 44×44pt，最小尺寸为 28×28pt；macOS 对应默认 28×28pt、最小 20×20pt。这不等于本产品要使用这些原生下限，也不能把 28pt 当成通用推荐按钮尺寸；具体组件建议可能更保守。本产品自定触控目标 44×44 CSS px，Web 最低验收参考 W3C。
3. 苹果当前 Layout 在 macOS 提出避免底部放置关键控件。本产品已有长表固定保存栏和验收契约，因此保留单一提交栏，并要求实际高度测量、安全留白、软键盘和低矮窗口适配。不是“因为苹果”再加一个顶部保存按钮。
4. 苹果工具栏倾向熟悉的系统图标；预算智解的复杂业务操作优先保留清晰中文短标签。不为外观更像苹果，把“查看影响”“保存新版本”改成无解释图标。
5. 苹果的字体和 SF Symbols 不作为可任意复制的 Web 资源包。使用系统字体和现有有许可证的图标；新增字体或资源先独立核对许可。
6. 不为追求简洁隐去数据核对依据，也不恢复每条都人工确认的流程；沿用已确认规则及当前确认门槛。
7. 深色主题、完整 ARIA grid、列宽拖动、界面密度选择器是可能的后续能力，不因官方提及就加入本次实施范围。

## 3 仓库核对结果

核对的是当前工作树，不只依赖提交 71d6399。工作树包含此前已授权的多轮未提交改动，本轮不得整理、覆盖或一并提交这些改动。

| 现有实现 | 可以复用或需收敛的地方 |
| --- | --- |
| `apps/web/src/HistoricalBudgetPage.tsx` 与 `styles.css` | SKU 使用 h2 与 history-section，标题基准 23px、完整容器 |
| `apps/web/src/NextYearBudgetPage.tsx` 与 `nextYear.css` | SKU 使用 h3，ny-section 标题基准 15px，表格外壳单独存在 |
| `apps/web/src/fy27Navigation.css` | 已有共享页面标题与导航规则；不能宣称全产品完全没有规范 |
| `apps/web/src/BudgetPageNavigation.tsx` | 已测量导航实际高度、处理当前项和阅读位置，继续复用 |
| `apps/web/src/BudgetScopeBar.tsx` | 已统一筛选，调用方负责数据和 URL；筛选不是预算编辑 |
| `apps/web/src/ActionTable.tsx` | 已有复制、导出、键盘选区、中文输入法和编辑元素保护；不要重写一份 |
| `apps/web/src/QuarterlyBusinessTable.tsx` 及季度布局工具 | 已有同业务跨季度阅读，应保留业务键和固定列逻辑 |
| `apps/web/src/NextYearBudgetPage.tsx` | 编辑由角色与历史状态共同决定；统一提交栏、基准预览及来源哈希校验均需保留 |
| `apps/api/routes/next_year.py` | 已确认来源、快照核验与预览只读是后端契约，展示规范不得取代它们 |
| 现有前端组件测试 | 多处把 CSS 当空模块，适合行为断言，不足以发现字号和容器差异 |

上一轮两个区块在同一浏览器宽度实测字体家族相同，字号分别为 23px 和 15px，表格正文分别为 14px 和 13px。这支持“组件和视觉层级分散”的判断，而不是将差异归因于浏览器缩放。后续实施仍应对实际改造版本重新截图与测量。

原有交互方案 [年度预算后三模块交互优化方案](../product/three-module-interaction-redesign.md) 部分记录较早，并包含原型阶段的导航和试用数据描述。新规范保留其中上下文、证据和版本保护原则，但当前四模块导航和本文规范优先；不得拿旧原型文档反推当前真实数据或已上线能力。

## 4 颜色可行性检查

对 sRGB 基线使用相对亮度公式计算，结果保留两位仅用于说明。正式验收必须使用浏览器最终前景、背景、透明度与状态。

| 前景与背景 | 计算对比度 | 判断 |
| --- | --- | --- |
| `#192521` / `#fbfcf8` | 15.35:1 | 主要正文基线可用 |
| `#66726d` / `#fbfcf8` | 4.86:1 | 辅助正文基线可用 |
| `#66726d` / `#eef0eb` | 4.36:1 | 页面底上的普通小字不达基线，不能只验证卡片背景 |
| `#5f6d65` / `#eef0eb` | 4.74:1 | 规范采用的次要文字色，页面底色上可用 |
| `#5f6d65` / `#fbfcf8` | 5.28:1 | 新次要文字色，内容表面上可用 |
| `#173f37` / `#ffffff` | 11.65:1 | 主按钮反白基线可用 |
| `#a86518` / `#f5e9d6` | 3.86:1 | 不用于普通小字警告 |
| `#a23f35` / `#f6e4e0` | 5.18:1 | 错误文字基线可用 |
| `#7c8a80` / `#ffffff` | 3.62:1 | 控件必要边界基线可用 |
| `#80501c` / `#f5e9d6` | 5.68:1 | 新警告文字基线可用 |

这不是全站无障碍合规结论，也不证明所有历史 CSS 组合已达到要求。

## 5 独立审核与实施界限

由未参与起草的独立子代理审核主规范。审核重点包括来源归属、原生与 Web 的边界、当前组件复用、数据和版本保护、复制导出范围、键盘与辅助技术、窄屏固定栏以及可执行验收。

当前研究与规范文档不修改产品页面、后端、预算数据、计算和定版规则，不代表已经发布或上线。后续实际迁移按主规范 M1 至 M4 分批进行。独立审查的问题、处理和复审结论记录在 [规范审核记录](review-2026-10-14.md)。
