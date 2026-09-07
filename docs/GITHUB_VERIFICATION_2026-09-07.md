# GitHub 验证记录

候选提交：`e878f83801ef15197a984a78d62e61bfb6e2bce8`。

- [PR #1](https://github.com/jiangmei-yang/uk-visa-agent-demo/pull/1)：普通 Gmail 收件、顾问接待、文件恢复与当前评测证据。
- [分支 CI](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34090920830)：已成功完成，verify 耗时 4 分 47 秒。
- 通过步骤：锁定依赖安装、全仓库 lint、类型检查、全部 pytest、示例生成、稳定性检查、全新容器启动、健康检查、ZIP 下载校验和 HTTP 交付门禁检查。
- 稳定性日志：5 次独立运行生成同一 ZIP SHA-256 `4b2018538fca16275ed69aac429f886a717e2f67cd4a9547808977ddb189cefd`；100/100 并发控制台读取通过。
- HTTP 冒烟测试的 `all_passed` 和 `zip_crc_valid` 均为 true。

此记录证明上述提交在 GitHub Linux runner 上通过，无模型或邮箱密钥参与 CI。
不把它称为真实 Gmail 最终交付或独立准确率测试。PR 合并结果及默认分支检查需另行核验。

## 后续诊断改进

PR 事件的独立检查 `34090941453` 在测试阶段运行明显更久，观察时尚无完成日志。
没有将其认定为失败或重启。后续 CI 命令增加 pytest 60 秒线程栈诊断、
最慢 20 项耗时和 runner 本地 JUnit XML；整个 job 设置 30 分钟上限。
这些设置不排除测试、不修改断言，也不影响当前已启动的任务。
新命令已在本地接待回归上验证：26 项通过，XML 和耗时输出均生成。

该旧任务随后自行成功结束：06:27:49–06:44:20 UTC；测试为 5,600 passed、
6 skipped、1 warning，947.25 秒。稳定性、100/100 并发读取和容器冒烟也通过。
后续尝试清理过时任务时，GitHub 因其已经完成而拒绝取消，没有运行被取消。
不能把这次耗时记为已证实死锁。另有缓存占用冲突提示，不影响该任务的 success 结论。

## Linux 诊断复跑

另以 `29988bf` 的受版本控制文件构建镜像 `966849d0320a`，创建无宿主挂载的
`visa-linux-regression-29988bf` 容器。仅安装开发依赖时联网，随后断开 bridge；
没有复制密钥或客户数据库。完整测试逐项输出，没有出现停滞，141.12 秒结束：
5,598 passed、6 skipped、2 failed。6 项为缺少 zsh 的 macOS 启动器测试；
2 项为 saved sponsor/employer probe 调用 Git，但精简镜像没有 Git。
这是诊断环境依赖差异，不能据此写成 Docker 全量测试通过，也未删除失败测试。
日志：本机 `/tmp/visa-linux-regression-29988bf.log`；容器已自然退出并保留。

## 已合并版本

`29988bfe5a0eac5c9860c757901ee8e4436d5a6e` 的
[分支检查](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34091829043)
和 [PR 检查](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34091832011)
均成功，包括全部回归、稳定性和容器交付验证。
2026-09-07 06:45:09 UTC，PR #1 按匹配的 head 提交合并；GitHub 默认分支已核验为
`6e8527a4f8867d712a4a779fc4e9dd64c1592ffc`。这是代码合并成功，
不代表真实 Gmail 最终包和完整录屏已完成。

[合并提交 CI](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34092232529)
随后已成功结束，verify 耗时 4 分 41 秒；全部测试、稳定性、容器和 ZIP 门禁验证通过。

## 尚未取得的验收证据

真实旅游邮件案 `case-9b9082b76a2d` 的只读检查仍为 DRAFT，摘要及最终摘要未确认，
无交付路径，两个身份类文件问题仍开放；原流水姓名矛盾已解决。
没有伪造文件接受、客户确认或最终邮件。独立虚构演示案件与真实材料路线的选择
已请求用户确认，尚未收到答复；最终 Gmail ZIP 收件及录屏不能宣称完成。
