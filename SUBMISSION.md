# 英国签证服务 Agent — 面试交付入口

这是一个 Email 优先、帮助客户准备英国 Standard Visitor 申请材料的受控 Demo。
交付物是**待人工复核的材料 ZIP**，不是替客户向英国政府提交申请，也不是获签保证。

> 开发候选状态：本地验证与 GitHub `main` 的历史 CI 不等同。
> 当前已保留 22 回合真实 DeepSeek 对话及 4 份虚构财务 PDF 的新实验；
> 完整自动测试不再排除旧报告检查。真实 Gmail 收件人侧最终包验收和
> 未受指导的面试官试用仍未完成，不能将此页视为完成声明。
> 最新交付核对见 [开发候选验证](docs/SUBMISSION_CANDIDATE_2026-09-07.md)。

## 面试官先看这里（约 5 分钟）

1. 下载仓库 ZIP 并解压，安装并打开 Docker Desktop。
2. Mac 双击 `START_DEMO.command`；Windows 双击 `START_DEMO_WINDOWS.bat`。
3. 浏览器打开后，点击 **Try the offline workflow**（离线流程体验）。
4. 依次发送初始邮件、补件邮件，查看冲突如何被发现和解除。
5. 核对摘要并确认，然后下载材料包。补件完成但尚未确认时，仍不能下载。

首次启动需要联网下载依赖，之后这个离线示例不需要 API key 或邮箱登录。
这里使用可见的虚构邮件和材料，**不是任意问题自由聊天，也不会真的发送邮件**。
具体启动、停止和排错见 [START_HERE.md](START_HERE.md)。Mac/Docker 已复验；
Windows 有启动脚本，原生 Windows 双击体验仍待独立验证。

## 与笔试三项要求的对应关系

| 要求 | 当前交付内容 | 证据与边界 |
|---|---|---|
| Email / WhatsApp 顾问准备材料 | Gmail 收取、独立案件、补件、纠错、确认、审核后 ZIP 发送；WhatsApp adapter / webhook | Gmail 已有真实试收发记录；当前全流程主要由离线及捕获发送测试验证。WhatsApp 无真实设备验收 |
| AI 稳定性及 workflow 设计 | DeepSeek 结构化提取、来源校验、持久化状态、幂等 outbox、确定性规则和确认门槛 | [架构](ARCHITECTURE.md)、[验证台账](VALIDATION.md)、[失败与改进记录](CONVERSATION_REVIEW.md) |
| GitHub repo 提交 | 源码、依赖锁文件、启动脚本、Docker、CI、测试、虚构材料和实验报告 | 不包含 API key、OAuth token、真实客户邮件或私人案件数据库 |

客户只面对一个连续的顾问。模型通过 API 接入，并非自行训练的基础模型；内部按提取、
校验、指导、状态推进、交付分工，而不是让多个自由聊天机器人互相决定业务结果。
已知事实、明确未定的日期和已发送问题会保留；有来源的建议先回答客户当前问题，再问
必要的下一项。无法验证的材料、模型失败、信息冲突或过期政策不能靠流畅措辞放行。

## 真实 Gmail 怎么测

这是独立于网页的、有注册发件人限制的监督运行模式。
先按 [Gmail 自动服务说明](GMAIL_AUTOMATIC_SERVICE.md) 配置自己的授权邮箱、DeepSeek
和允许的测试发件人。普通主题和自然措辞均可，不要求写“测试”或使用固定申请模板。

首次处理前，申请人需要在原邮件线程收到当前处理告知并回复其中的同意参考码。
邮箱 OAuth 授权和申请人的材料处理同意是两回事；不能由操作员代写同意或修改数据库。
之后可直接自然回复、补件和纠错，不必每封重复参考码。最终 ZIP 需操作员审核后发送。
不要向陌生发件人开放，也不要把真实护照当测试素材。

本地开发验证不代表已部署邮箱档案的授权或发送状态；恢复真实测试前须重新核对。
当前开发候选**不声称新的普通材料 Gmail 全流程已在收件人侧验收**。
历史证据和失败见 [GMAIL_LIVE_EVIDENCE.md](GMAIL_LIVE_EVIDENCE.md)。
WhatsApp 接口和配置见 [WHATSAPP_SANDBOX.md](WHATSAPP_SANDBOX.md)，真实收发另行验收。

## 开发者复验

```sh
uv sync --extra dev --locked
make lint
make typecheck
make test
```

CI 还会实际构建 Docker 并运行 `scripts/release_smoke.py`，检查初始阻断、乱序拒绝、
重复点击、补件后的确认门槛、最终 ZIP CRC 和重复下载一致性。它只允许本机地址，
拒绝已经使用过的 lab，**不会重置已有数据**。本机手动运行时应使用新建的隔离容器，
传入其地址，例如 `uv run python scripts/release_smoke.py --base-url http://127.0.0.1:18080`。
不要把这一命令直接指向正在使用的客户实例。

## 完成标准与尚未声称完成的内容

这个仓库可以作为**范围明确、可运行、可审计的面试 Demo**提交，不是公开运营版本。
自动测试全绿不等于“自然度、准确率、可靠性 100%”。仍需真实同意后的普通材料全流程、
未受指导的面试官试用、真实 WhatsApp 设备测试；复杂流水、联合账户、所有扫描件和
全部签证路线不在已证明的能力范围。详见 [剩余验收项](docs/END_TO_END_ACCEPTANCE_AUDIT.md)。
