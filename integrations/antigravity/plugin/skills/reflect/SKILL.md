---
name: reflect
description: 基于近期新增记忆执行定期反思，生成结构化的用户画像更新提案与 diff，静默模式下自动应用。
---

# 画像反思与自我迭代（Reflect）

当用户主动调用 `/reflect` 或要求整理/刷新画像时，执行以下流程并展示结果：

1. 调用 `stats()` 检查 `extractor_mode`。
2. **api 模式**：
   - 调用 `reflect_run()` 生成提案（在默认 `auto_apply=true` 静默配置下会自动应用）。
   - 向用户展示本次反思摘要、变更 `diff` 以及生效后的新版本号，并告知如需撤回可调用 `profile_rollback(version)`。
3. **agent 模式**：
   - 调用 `reflect_prepare()` 获取当前完整画像、提示词与上次版本以来的新增记忆（最多 80 条）。
   - 按照返回的 `prompt` 生成最小且准确的 `patch` 列表和 `note`，调用 `profile_propose(patch, note)`。
   - 若开启自动应用则直接调用 `profile_apply(proposal_id)` 并展示已应用的 `diff` 与新版本号；否则展示 `proposal_id` 与 `diff` 等待确认。
4. 若用户要求撤回某次更新，调用 `profile_rollback(version)` 恢复。
