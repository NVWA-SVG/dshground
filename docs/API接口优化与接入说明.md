# API 接口优化与接入

日期：2026-11-12

## 已调整

- 修正主模型名称校验，支持 `Qwen/...` 这种完整 ID。主模型仍以当前 DSH 通道已登记模型为准；未接入的模型在保存前明确拒绝，不在生成途中才发现。
- 「检查连接」实际向 SiliconFlow `/models` 发起带认证的 GET，核对当前模型可见性。运行时静态目录不再冒充远端认证通过。检查不创建生成任务，也不声称已经验证工具调用质量、余额或整套生成能力。
- 备课、识图和生图按模型分别排队，默认同一模型并发 1、请求间隔 1000 毫秒；429 后默认冷却 60 秒，供应商返回有效 `Retry-After` 时优先遵守。可在模型设置的「高级：API 请求节流」调整。
- 已保存设计与视觉方案通过 DSH 的 `concludeTurn()` 正常结束，保留工具成功回执，不再额外向模型请求结束语。校验失败仍按原有次数限制修复，不提前结束。
- 识图和生图采用同源 JSON 接口层，统一超时、响应体大小上限、取消、代理、空响应/HTML/无效 JSON 处理。已收到 HTTP 错误时，响应体卡住也保留该 HTTP 错误，避免再次误报为普通超时。
- 402、401、403 等识图设置/额度错误暂停队列后续消费，默认逐张处理。已有描述、缓存和回写恢复机制保留。识图对明确的 429 仍沿用最多两次重试；结果不明的识图、文本、生图请求不自动重发。
- 视觉模型可沿用主模型密钥，也可使用独立 `VISION_API_KEY`。独立视觉设置不会覆盖 `SILICONFLOW_API_KEY`。非 SiliconFlow 视觉地址需要独立密钥，并支持 HTTP 代理与兼容 Chat Completions 接口。
- 生图增加请求格式选择。SiliconFlow 使用 `image_size`、`num_inference_steps`；OpenAI 兼容格式使用 `size`、`n=1`，按供应商要求填写图片尺寸。旧生图任务保持原配置，已生成图片及下载回执继续复用。
- 接口错误保持教师可读的 `error`，补充 `code`，以及可获得的 `request_id`、`retry_after_ms`。上游响应体和密钥不回显到错误提示；供应商提供的请求 ID 可用于排查。

## 使用入口

1. 左下角「模型设置」：保存主模型密钥，点击「检查连接」；展开高级 API 节流可调并发、间隔、冷却时间。留空密钥保留已有值。
2. 「知识库 → 视觉模型设置」：选择共享或独立密钥、模型、Base URL 和可选代理；「检查已保存的连接」只读模型目录，不发送教材图片。
3. 「课件外观与服务 → 生图模型与 API 设置」：选择 SiliconFlow 或兼容服务，并为其他服务选择对应请求格式。原教材素材与本地可编辑 PPT 装配流程不变。

已有连接与密钥无需重新填写。课后记录依旧在本地完成，不调用模型、不生成旧课修订。

## 本地接口

写接口继续要求本机来源及 `X-Teacher-Token`；密钥只在本地设置页填写。

| 接口 | 用途 |
|---|---|
| `GET /teacher/api/api-policy` | 当前节流设置、执行中/等待数和本机接纳请求计数 |
| `POST /teacher/api/api-policy` | 保存 `max_concurrent`、`min_interval_ms`、`rate_limit_cooldown_ms` |
| `POST /teacher/api/settings` | 保存主模型与可选新密钥 |
| `POST /teacher/api/test-connection` | 实际验证远端认证与当前模型可见性 |
| `GET/POST /teacher/api/vision/settings` | 视觉配置，含 `credential_source=shared/separate`、`proxy_url` |
| `GET /teacher/api/vision/models` | 检查已保存视觉连接，返回 `model_available` |
| `GET/POST /teacher/api/image/settings` | 生图配置，含 `api_format=siliconflow/openai` |

`config/api-policy.json` 仅在保存节流设置时创建；主模型、识图及生图仍各自保存公开配置，密钥继续进入 DSH 凭据库，不写入配置 JSON、课程或提示词。

## 验证范围

相关接口测试通过，涵盖完整模型 ID、静态目录不能替代认证、无效密钥、并发与取消、429 冷却、Retry-After、已知 HTTP 错误的超时保留、异常响应、视觉独立密钥、402 分类、生图格式与缓存恢复。

浏览器设置流程通过。真实安装的 DSH 工厂、预设、工具与 pi-ai 路径通过本机模拟服务完成整套修订和交付；同一模拟流程的请求由 6 次变为 4 次，所有请求均经过新调度器。此计数只说明该测试流程，实际 token 与计费取决于任务和供应商。

本轮 0 次外部模型调用、0 次付费生成；未发送用户教材或学情。没有实测当前账号限额或联网生成质量。并发与冷却不能提高供应商账号配额，仍应以供应商后台为准。

参考：[SiliconFlow 限流规则](https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)、[Chat Completions 接口](https://docs.siliconflow.cn/docs/api/chat-completions-post)。
