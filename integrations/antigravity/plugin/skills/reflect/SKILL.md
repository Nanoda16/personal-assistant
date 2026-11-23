---
name: reflect
description: 基于近期新增记忆执行定期反思，生成结构化的用户画像更新提案与 diff，经用户确认后应用并更新版本。
---

# 画像反思与自我迭代（Reflect）

当需要整理近期积累的新记忆、修正过时认知或提升高频技能置信度时，执行以下流程：

1. 调用 `stats()` 检查 `extractor_mode`。
2. **agent 模式**：
   - 调用 `reflect_prepare()` 获取当前完整画像、提示词与上次版本以来的新增记忆（最多 80 条）。
   - 按照返回的 `prompt` 生成最小且准确的 `patch` 列表和 `note`。
   - 调用 `profile_propose(patch, note)` 存入提案，**不要跳过确认直接应用**。
3. **api 模式**：
   - 调用 `reflect_run()` 自动生成提案与 diff。
4. 向用户清晰展示提案 ID（`proposal_id`）和可读 `diff` 摘要，等待用户确认：
   - 用户同意后，调用 `profile_apply(proposal_id)` 生效并刷新精简画像缓存。
   - 若用户要求撤回某次更新，调用 `profile_rollback(version)` 恢复。
