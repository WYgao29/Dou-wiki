# Qwen3 转写修复的两项审查问题

日期：2026-10-12。审查对象为提交 `77a3784`；用户要求修复两项已确认的回归，修复后复审，无问题再提交代码。

## 修复结果

`src/douyin_wiki/adapters/asr_segments.py` 修正两处失败分支：

- 局部预测先处理必须成功的主词组，再逐组检查附加词组。每组通过全部检查后才替换词；附加组缺少相邻词或时间不能衔接时，保持该组原状态，留给后续局部尝试。主组失败仍明确报错，最终仍要求全部异常词修复成功，不返回部分转写。
- 停顿中点不合法时，计算停顿与允许时间区间的交集，并在交集内选择距离中点最近的整数毫秒。允许区间同时包含名义切点前后 15 秒、两侧最多 180 秒、切点严格位于前一切点和音频终点之间的限制。上下界按采样点精度向区间内取整；数词保护与默认词尾选择行为保持原有规则。

两个审查复现均已加入 `tests/test_asr_recovery.py`。附加组缺少邻词时，主词 54 的修复保留，随后第二次预测修复词 79 及必要相邻词；停顿覆盖 164.960–169.920 秒、中点 167.440 秒越界时，在允许区间末端 165 秒切分，完整流程成功。

新增 13 项回归覆盖附加组缺邻词、组内重叠、再次预测仍失败时整条失败、正负 15 秒边界、两侧 180 秒边界、单采样点取整、严格切点端点、移动锚点及数词保护。首批用例在修复前为 9 failed / 3 passed；第 13 项补充验证附加组进入下一次预测后仍不能通过时，整条任务继续失败。

`CHANGELOG.md`、`docs/architecture.md` 与文档索引同步说明当前行为。模型、依赖、MCP / Web 契约和用户知识库格式未调整。

## 验证与复审

相关回归命令：

```bash
uv run --no-sync pytest -q tests/test_asr_recovery.py tests/test_asr_segments.py tests/test_qwen3_asr.py tests/test_asr_text.py tests/test_asr_migration.py tests/test_verify_qwen3_asr.py tests/test_media.py tests/test_media_models.py tests/test_gateway_context.py tests/test_gateway_submission.py tests/test_pipeline_state.py tests/test_review_pipeline_fixes.py --tb=short
uv run --no-sync ruff check src tests
git diff --check
```

结果为 **438 passed**，Ruff 和差异检查通过。其中 ASR 分段、恢复和 Qwen3 测试共 133 项，包含本轮新增的 13 项。

复审重点为组替换是否只在检查成功后发生、主组和最终整段校验是否继续拒绝无效时间、剩余局部预算是否仍计实际尝试，以及切点是否合法且保留原文归属。未发现新的需修复问题。

仓库外 `review_checks.py` 用固定随机种子生成 2000 组不同停顿、名义时间和采样点端点，枚举每个停顿的全部整数毫秒作为独立对照。1198 组存在合法交集并成功选点，802 组正确拒绝；相同 2000 组输入下，默认词尾选择结果与修复前完全一致。

同时回放之前四条视频保存的 44 次识别 / 对齐输出，逐次核对调用顺序、音频样本数、参数和原文。转写段、时间、原文归属、切点及局部修复记录与已通过的结果完全一致，段数保持 25 / 18 / 21 / 1。此项是已有模型输出的回放，没有重新运行真实模型或重新处理生产任务。

## 安装包与本机运行

实际 wheel 在仓库外临时环境中离线安装，仅使用已有 runtime 和开发环境的依赖目录，不同步或升级依赖。核对导入来自新环境的 `site-packages` 后，ASR 分段、恢复、Qwen3 与 Gateway 上下文测试为 **171 passed**；90 个运行文件逐字等于源码和安装包。

先保存当前 runtime 包及配置、依赖和 LaunchAgent 信息，确认没有排队或运行任务，再停止 Web / Worker，离线仅替换抖库 wheel，重新启动两个服务。安装后 90 个运行文件再次核对通过；依赖版本、配置和三个 LaunchAgent 文件哈希保持不变。本机仍为包 `0.2.1` / Web `0.2.50`，新的 Worker ID 为 `worker-ab4f4f5fea874ffeb280153f17f07e28`。Web / Worker 健康，无排队或运行任务；业务任务计数保持更新前状态。

没有重新转写或重试已成功入库的四条视频。独立验收环境在保存结果后删除，安装包、回放脚本和部署恢复材料继续保留。

## 验证材料

本轮仓库外目录：`/Users/weisengao/Documents/ChatGPT/maintenance/douyin-wiki/qwen3-review-fixes-2026-10-12-8l_u23g8/`。

`review_checks.py` 与 `review-checks.json` 保留边界枚举和四条记录回放结果。原审查及父版本 / 新版本对照保留在 `qwen3-review-2026-10-12-tb7vbapi/`，原音轨、真实模型输出和第一次修复的部署恢复材料继续保留在 `qwen3-failure-repair-2026-10-12-m7aa3r4r/`。

`wheel-check.json`、`dist/douyin_wiki-0.2.1-py3-none-any.whl`、`runtime-before.zip`、`runtime-before.json`、`deploy_asr.py`、`runtime-install.log` 和 `deployment.json` 保留本轮安装包验收及更新前恢复材料。安装包 SHA256：`15dab9217afc5f6b79073f488a36598502f004ac21967e492a1f3b87aeda86d1`。
