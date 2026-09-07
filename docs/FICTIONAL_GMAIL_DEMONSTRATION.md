# 独立虚构 Gmail 演示（实现中）

用户已确认采用独立虚构演示案件完成真实邮箱收发和录屏，保留样本标识，
不把演示资料包用于实际申请，也不放宽普通客户的校验。

## 已实现的登记边界

- 操作者登记绑定一个已建立的 Gmail 案件、线程、客户联系方式以及身份样本哈希。
- 仅支持登记护照和居留身份样本，不允许登记资金文件以规避资金校验。
- 只能在该独立案件收到任何文件之前登记；不能转换已有材料、已确认、暂停或人工处理案件。
- 登记不可修改；重复同一登记返回原记录，改线程、所有者、样本或操作理由则拒绝。
- 登记不修改案件、不标记客户同意、不清除问题、不确认摘要、不生成或发送材料包。

## 已接入本地代码，尚未部署

1. 登记与当前普通材料读取器组合：仅精确匹配登记样本才能进入模拟身份核对；
   文件事实仍须有来源，不以样本登记接受错误、缺失或矛盾信息。
2. 演示标识随案件、证据、摘要、ZIP 和下载/发送检查完整传播，避免演示包被当作真实材料包。
3. 在同一 Gmail 收件服务内按案件路由，避免两个 worker 重复回复，其他客户维持原行为。
4. `scripts/register_fictional_case.py` 在共享 worker 锁内登记，解析失败回滚，不调用模型或发送邮件。
5. 按最新展示要求，生成 PDF 不再逐页显示虚构演示标识，仅首页说明使用示例资料；
   身份样本原件的标记不变。ZIP 保留案件清单，发送和下载校验清单及完整性。

登记示例（必须先存在独立 Gmail 案件，且尚未收取附件）：

```sh
.venv/bin/python scripts/register_fictional_case.py \
  --state-dir data/gmail-live-user --case CASE_ID --thread THREAD_ID \
  --sender EXACT_STORED_CONTACT --operator OPERATOR \
  --reason 'User approved this independent fictional demonstration' \
  --specimen passport=/absolute/path/passport.pdf \
  --specimen status_document=/absolute/path/residence_status.pdf
```

不要用此命令转换普通客户，也不要在 worker 正在持锁时另开收件进程。

## 尚未完成的真实验收

- 当前版本模型评测及最终完整回归。
- 部署当前代码到唯一 Gmail worker。
- 建立新的独立邮件线程，不转换现有 `case-9b9082b76a2d` 的文件或确认状态。
- 真正走完收件、补件、纠错、客户检查摘要、指定案件发送、客户邮箱下载检查及录屏。

本轮目前没有发送新邮件，也没有改变正在运行的真实客户服务。
