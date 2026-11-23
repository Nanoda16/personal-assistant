---
name: memory-ingest
description: 扫描并增量抽取 Antigravity 会话历史或外部应用导出数据（ChatGPT conversations.json、微信/笔记文本），将长期记忆与事件摘要写入本地记忆库。
---

# 记忆采集与导入（Memory Ingest）

## 1. 检查运行模式
先调用 `stats()` 查看 `extractor_mode`：

- **情况 A：`extractor_mode == "agent"`（默认，当前会话抽取）**
  1. 调用 `ingest_scan()` 扫描最新会话与 Hook 队列。
  2. 循环调用 `ingest_next()` 获取待处理会话文本与抽取提示词（`prompt`）。
  3. 严格按照 `prompt` 中的 JSON 格式要求，自行提炼 `episode` 与 `memories`（只保留关于用户本人的长期有效信息）。
  4. 调用 `ingest_commit(ingest_id, result)` 入库。
  5. **每次调用默认最多连续处理 5 个会话**，每批完成后向用户汇报进度与新提取的关键记忆条数，询问是否继续处理剩余队列。

- **情况 B：`extractor_mode == "api"`（服务端直连 OpenAI 兼容 API）**
  1. 调用 `ingest_scan()`。
  2. 直接调用 `ingest_run(limit=5)` 由后端自动完成抽取与入库，并汇报结果。

## 2. 外部应用历史导入
当用户提供 ChatGPT 导出的 `conversations.json`、微信聊天导出文本或 Markdown/TXT 笔记目录时：
1. 调用 `ingest_file(path, kind="auto"|"chatgpt"|"text")` 登记或导入。
2. 若当前为 `agent` 模式，随后按上述 `ingest_next` → `ingest_commit` 流程完成抽取；若为 `api` 模式，会自动完成抽取。
