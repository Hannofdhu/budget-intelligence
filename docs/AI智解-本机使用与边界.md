# AI智解

入口：`/budget/ai`。位于“下一年预算”之后，与其他模块共用导航和前端样式。

## 能做什么

- 用中文理解产品、核对已保存预算、分析预测缺口和下一年预算。
- 使用真正的 Pi 原生终端，而不是只实现一部分能力的聊天接口。保留原生命令、模型选择、工具调用、会话分支、历史回看、上下文压缩、技能与扩展机制。
- 在独立工作区读取、编写和执行分析脚本；通过 `budget-api` 查询产品数据。
- `/model` 选择模型，`/resume` 回顾原生会话，`/tree` 查看分支，`/hotkeys` 查看快捷键。

首次进入不会启动 Agent 或调用模型。点击“开始对话”后启动；切换模块只断开显示连接，再进入可以继续原工作会话。点击“停止会话”才结束运行。历史记录保存在本机独立目录；新会话可用 `/resume` 回顾。浏览器不保存模型密钥或连接票据。

每个工作会话的分析文件空间独立；`/resume` 恢复的是对话记录，不会自动复制旧工作区生成的文件。旧工作区文件仍保留在本机 AI 目录中，不会因停止会话而删除。

## 不能做什么

- 不能读取或修改产品开发文件、宿主机任意文件、OpenClaw 凭据文件。
- 不能直接连接数据库，也不能保存预算、刷新预测、自动调平、确认、发布或改变定版规则。
- 不能任意联网。模型与获准的产品读取走后端受限桥接；容器没有网络接口或宿主服务端口。
- 浏览器中的外部编辑器、系统快捷键、原生图片/剪贴板协议不与本机终端完全等价。联网安装包和扩展不可直接使用；已安装的原生工具及扩展机制在隔离范围内运行。

因此，这是 **Pi 原生交互 + 本机隔离的产品使用助手**，不是具有宿主机全部权限的开发 Agent。提示词不是安全边界，限制由容器与后端白名单执行。

## 本机准备

需要 Docker/OrbStack 正常运行。模型由 API 只读解析当前用户的 `~/.openclaw/openclaw.json`，不复制原文件，不修改 OpenClaw。

```sh
docker build -t budget-pi-agent:1.2.0 apps/ai-agent
.venv/bin/python scripts/local_trial.py restart-api
.venv/bin/python scripts/local_trial.py restart-web
```

两个 restart 命令只重启已登记且身份匹配的服务；不会初始化数据库、迁移、取数或重算预算。平时按原本启动流程使用即可。没有本机 OpenClaw 模型配置的同事需要自行准备自己的配置，不会获得你的密钥。

启动器生成 `output/local-trial/ai-proxy.secret`，仅供 Vite 与 API 验证请求，文件权限为 `0600`，不提交 Git。AI 默认关闭，只有显式启用的本机环境可访问；局域网、伪造来源、非同源连接均拒绝。生产环境当前不开放，不能把本机身份头作为多用户认证。

## 模型与凭据

读取 OpenClaw 的主模型、已定义模型、兼容参数及备用顺序。仅接受 OpenAI Chat Completions 协议；其他协议明确报告未配置，不默默改协议。

本机网关实测不接受 Pi 的默认 `developer` 消息角色。桥接模型默认使用兼容的 `system` 角色；明确的模型级兼容配置仍优先。这个调整只作用于模型协议，不修改 OpenClaw 文件或预算规则。

容器内的 `models.json` 只有容器回环地址和固定占位值。真实密钥、上游地址和必要认证头只在服务端内存中使用，不进入前端、Docker 参数、Docker 环境或容器文件。

默认主模型在连接失败或返回允许的临时错误时，可在响应开始前按 OpenClaw 备用顺序切换。已开始的流不会自动重放；手动选择的其他模型不会被悄悄切换。后端状态提供实际响应模型用于诊断。上游 HTTP/HTTPS 设置沿用本机配置，不代表新增了加密传输。

## 产品只读接口

```sh
budget-api get help
budget-api list
budget-api get fy27-budget --output fy27.json
budget-api get fy27-forecast
budget-api get fy28-budget
budget-api get next-year-catalog
budget-api get fy27-forecast-versions
budget-api get fy28-budget-versions
budget-api get fy27-forecast --version VERSION_ID
budget-api get fy28-budget --version VERSION_ID
```

服务端仅映射这些资源到已有 GET 接口，继承原有本机访问、部门和版本校验。Agent 不能选择身份、方法、任意 URL 或 SQL。所有回答应以返回的财年、版本、单位、状态和时间为准；读取不会触发观远取数或重算。

## 运行与历史

`output/ai-agent/` 保存独立工作区与 Pi 原生历史，不属于预算账本，也不提交 Git。可能包含你在对话中使用的业务数据，请按本机敏感文件管理。

容器使用 `network none`、只读根目录、非 root 用户、移除能力、禁止提权及内存/进程/CPU 限额。不挂载项目、数据库、Docker socket 或 OpenClaw 文件。仅挂载 AI 工作区和独立 Pi 配置/历史目录。并发数有限，长时间无输入会自动停止；接口关闭时回收子进程与容器。停止不会删除历史。

Docker 不可用时展示不可用原因，不降级为宿主机运行。连接中断可以重新连接，不会自动重发问题。较早终端回放清理后可通过 Pi 历史查看。

## 开源归属

Pi 使用 MIT 许可证，集成固定版本 `@earendil-works/pi-coding-agent@1.2.0`，保留 npm 包原许可证，不复制第三方源码进业务计算层。

- [Pi 官方仓库](https://github.com/earendil-works/pi)
- [Pi 隔离运行说明](https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/containerization.md)
- [Pi 安全说明](https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/security.md)
- [Pi 模型配置](https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/models.md)
