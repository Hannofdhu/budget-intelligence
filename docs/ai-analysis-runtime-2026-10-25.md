# AI 智解分析运行环境验收

日期：2026-10-25。范围：隔离镜像预装分析依赖、工具能力说明、离线 Excel/绘图/脚本测试。没有修改预算计算、定版、数据库结构或前端功能。

## 已启用的环境

本机默认 `budget-pi-agent:1.2.0` 已指向验收过的新镜像，镜像 ID 为 `sha256:1f173311cc947e2b9cfd2aaef281ba6b76a0f495947b49ea3512508ea43b99a3`。旧镜像保留为 `budget-pi-agent:before-analysis-20261025`，没有删除原工作区、历史或中断用户会话。后续启动容器使用新镜像，已运行容器不自动切换；不需要重启 API、刷新预测或重算预算。

实测 Python 3.11.2、Node.js 24.22.1、Pi 1.2.0。

| 依赖 | 固定版本 | 用途 |
|---|---|---|
| Pandas | 3.1.0 | 表格整理、分组与汇总 |
| NumPy | 2.4.6 | 数值分析 |
| OpenPyXL | 3.1.5 | `.xlsx` 读取、编辑与公式保留 |
| XlsxWriter | 3.3.0 | `.xlsx` 报告输出 |
| Matplotlib | 3.11.2 | 无桌面 PNG/SVG/PDF 绘图 |
| Seaborn | 0.13.2 | 统计图表 |

17 个直接和间接 Python 分析依赖均锁定版本和 SHA-256；仅在镜像构建时安装 wheel，不在对话中联网安装。新增依赖独立于 API 和业务计算环境。字体使用 Noto CJK，Agg 无桌面绘图，缓存写 `/tmp`，限制数值库并行线程，保留原 2 核/1GB/128 进程约束。

## 实际执行的验收

- AI 运行、桥接、聊天、排队、知识和 API 契约回归：278 项通过；保留一个既有 Starlette/httpx 弃用警告。
- 实际镜像测试：候选镜像和本机默认镜像均通过，直接复用 `RuntimeManager._container_args` 的生产参数。
- Excel：中文及前导零编号往返不变，数值与分组汇总正确；读源文件前后哈希一致；不可信文本未自动变公式；另存报告保留公式；中文 CSV 往返通过。
- 绘图：默认 Agg 与中文字体检查通过，PNG 实际解码、SVG/PDF 文件格式检查通过；中文图形已目视检查，无缺字。
- 脚本：真实 Python/Node 文件运行正确；错误返回非零；测试子进程超时被捕获。没有为任意 AI 脚本新增或宣称统一超时策略。
- 隔离：非 root、只读根目录、无可用外网、Docker socket 不存在，仅保留原两个 bind 挂载和临时目录；拒绝工作区外写入。
- 真实受限桥接配合模拟读取器：帮助返回新增环境说明；模拟预算载荷原样返回、不混入环境字段；POST 预算请求被现有桥接拒绝为 405，未调用读取器。未连接真实模型、预算数据库或真实预算接口。
- Ruff 和新增文件格式检查通过；项目 Mypy 225 个源文件通过。镜像独立 smoke 脚本由真实 Python 3.11 镜像执行验证，不向 API 的 Python 环境安装分析依赖或类型桩。
- 新增锁定 Python 分析依赖的 pip-audit 未发现已知漏洞；这不等于对基础镜像或全部产品依赖完成安全审计。
- 预测和下一年预算的 44 个台账文件，改动前后 SHA-256 一致。

## 可复测与交付边界

执行 `make ai-analysis-test` 可构建独立测试镜像并复用相同的真实容器验收。CI 已加入此检查，但远端 GitHub CI 尚未运行，本次未提交或推送。

测试记录在 `output/ai-analysis-2026-10-25/`：`regression.xml`、`image.xml`、`default-image.xml`。模拟 Excel、CSV、PNG/SVG/PDF 和测试脚本保存在该目录的 `proof-run/test_analysis_scripts_excel_pl0/workspace/proof/`，均标识为模拟数据，不是业务预算成果。

支持 `.xlsx` 读写、样式和公式保留，不包含 Excel/LibreOffice 公式重算引擎，也不新增上传、下载或文件预览界面。运行环境可用不代表任意 AI 生成的代码、数值分析或业务结论必然正确。

依赖维护方式参考 [pip 的哈希校验安装说明](https://pip.pypa.io/en/stable/topics/secure-installs/)，绘图缓存和后端配置参考 [Matplotlib 官方环境变量说明](https://matplotlib.org/stable/install/environment_variables_faq.html)。

## 后续补齐（2026-10-26）

以上“未新增下载或预览”是本次运行环境交付时的历史范围。2026-10-26按用户授权另行补齐结构化对话的文件卡片、同源下载和图片预览，旧对话的已有文件可直接读取，无需重新提问或重算；预算只读和隔离边界保持不变。后续真实下载与权限验收见 [AI对话文件交付记录](design/ai-chat-files-2026-10-26.md)。
