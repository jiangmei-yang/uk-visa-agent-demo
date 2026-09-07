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

PR 事件的独立检查 `34090941453` 在测试阶段运行明显更久，当前尚无完成日志。
没有将其认定为失败、取消或重启。后续 CI 命令增加 pytest 60 秒线程栈诊断、
最慢 20 项耗时和 runner 本地 JUnit XML；整个 job 设置 30 分钟上限。
这些设置不排除测试、不修改断言，也不影响当前已启动的任务。
新命令已在本地接待回归上验证：26 项通过，XML 和耗时输出均生成。
