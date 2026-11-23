---
name: memory-ingest
description: 扫描并增量抽取 Antigravity 会话历史或外部应用导出数据（ChatGPT conversations.json、Gemini Takeout 我的活动记录.json、微信/笔记文本），将长期记忆与事件摘要写入本地记忆库。
---

# 记忆采集与导入（Memory Ingest）

> 日常对话的增量抽取与反思已由 Stop hook 在后台自动静默完成，无需手动触发。本技能主要用于用户主动导入外部导出文件或手动排查队列。

## 1. 检查运行模式
先调用 `stats()` 查看 `extractor_mode`：

- **情况 A：`extractor_mode == "api"`（服务端直连 OpenAI 兼容 API）**
  1. 调用 `ingest_scan()`。
  2. 调用 `ingest_run(limit=5)`（或调用 `autopilot_run()`）自动完成抽取与入库，并在用户主动询问时汇报结果。

- **情况 B：`extractor_mode == "agent"`（当前会话抽取）**
  1. 调用 `ingest_scan()` 扫描最新会话与 Hook 队列。
  2. 循环调用 `ingest_next()` 获取待处理会话文本与抽取提示词（`prompt`）。
  3. 严格按照 `prompt` 中的 JSON 格式要求，自行提炼 `episode` 与 `memories`（只保留关于用户本人的长期有效信息）。
  4. 调用 `ingest_commit(ingest_id, result)` 入库。
  5. 每次调用默认最多连续处理 5 个会话，向用户汇报进度与新提取的关键记忆条数。

## 2. 外部应用历史导入
当用户提供 ChatGPT 导出的 `conversations.json`、Google Takeout Gemini Apps 导出的 `我的活动记录.json`（或其所在目录）、微信聊天导出文本或 Markdown/TXT 笔记目录时：
1. 调用 `ingest_file(path, kind="auto"|"chatgpt"|"gemini"|"text")` 登记或导入（`auto` 会自动识别 Gemini Takeout 与 ChatGPT 格式）。
2. 若当前为 `api` 模式，随后调用 `ingest_run` 完成抽取；若为 `agent` 模式，按 `ingest_next` → `ingest_commit` 流程完成抽取。
