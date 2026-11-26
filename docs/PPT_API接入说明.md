# 教师PPT技能与API接入

本模块把现有教学设计转换为逐页视觉提示词，交给PPT生成服务，再下载并核对成品。工作目录仍为 `C:\DSH`。教师入口中的「课件生成设置」配置服务；课程「教学材料」中可单独生成提示词、下载提示词、用保存的提示词生成图文PPT或继续查询原任务。

## 网络与代理

Windows浏览器可访问Gamma，并不表示DSH后台Node请求会自动使用系统代理软件。在「课件生成设置」填写软件提供的HTTP/混合代理地址，例如 `http://127.0.0.1:7890`，保存后立即生效。连接检查、任务创建、状态查询及PPT下载均使用此代理；不改变Windows全局设置，也不改变 Qwen 模型连接。

`proxy_url` 可通过本机POST `/ppt/settings` 保存。留空沿用运行时原有网络设置。代理地址不携带账号、密码或查询参数；代理端口必须是HTTP/HTTPS代理端口，不能填管理面板或仅SOCKS端口。

连接超时、域名解析失败、代理端口拒绝连接、密钥无效HTTP401和权限问题HTTP403分别提示。连接检查只读取主题列表，不创建课件或消耗生成积分。

## 实际工作流

1. Qwen 模型生成、校验并保存教学设计，原有Python程序导出基础材料。
2. DSH创建专用课件Agent，读取 `plugins/teacher-workbench/skills/teacher-ppt/SKILL.md` 和当前版只读教学brief。模型仅生成每页版式与配图要求，通过 `teacher_save_ppt_prompt` 工具提交。
3. Python校验逐页覆盖并保存 `课件提示词.md`、`visual-plan.json`、`brief.json`。正文、目标和分页来自原教学设计，不允许视觉方案重写。
4. 宿主 `generatePresentation` 读取保存的提示词与原文，通过PPT供应商适配器创建任务、轮询、下载。
5. Python校验页数、页序对应的全部标题和要点、可提取原生文字、16:9、文件结构；恢复原逐字稿备注后，生成独立图文课件和整套交付ZIP。

原课件、教学设计、信息密度及旧交付包不覆盖。图文稿与原材料属于同一教学版本，不把重排版伪装成一次学情优化。增加或改变教学内容须继续走学情、教师选择、新版设计流程。

## Gamma接入

在「课件生成设置」选择Gamma，本机填写API Key，保存后可点「检查连接」。检查调用GET `/v1.0/themes`，不创建生成任务。密钥保存至DSH凭据服务 `GAMMA_API_KEY`，不会回显或写入课件提示词、课程数据库和下载包。

配置文件 `config/presentation-api.json` 不含密钥。示例见 `config/presentation-api.example.json`。部署只在该文件不存在时创建，不覆盖已有设置。

支持两种Gamma模式：

| 模式 | 创建 / 查询接口 | 说明 |
|---|---|---|
| Classic，默认 | POST `/v1.0/generations`；GET `/v1.0/generations/{id}` | `textMode=preserve`、`cardSplit=inputTextBreaks`，按已有教学分页生成；默认AI配图 |
| Gamma 5 | POST `/v1.0/agent/generations`；GET `/v1.0/agent/generations/{id}` | 将提示词MD直接交给Gamma 5，支持质量档位与主题。页数是软目标，最终仍须通过本地检查 |

官方地址 `https://public-api.gamma.app`，鉴权头 `X-API-KEY`。两个模式均请求 `exportAs=pptx`；Gamma 5关闭工作区连接器，不请求邮件分享，外部分享设置为 `noAccess`。供应商方案或账号权限可能影响模型、主题、页数与额度。

Classic默认用于保留既有教学内容。Gamma 5视觉自由度更大，但可能改变页数或文字；未通过检查时不会标为交付成功，基础材料和提示词保留。下载已完成但未通过内容检查时，可单独下载明确标注的候选PPT进行人工核对；它不会进入已校验整套交付包。用户可调整风格或模式后明确创建新任务。

依据（2026-11-05核对）：[Classic创建](https://developers.gamma.app/generations-classic/create-generation)、[Classic查询](https://developers.gamma.app/generations-classic/get-generation-status)、[Gamma 5创建](https://developers.gamma.app/agent-gamma-5/create-agent-generation)、[Gamma 5查询](https://developers.gamma.app/agent-gamma-5/get-agent-generation-status)。

## 自定义PPT网关协议

选择「自定义PPT网关」，填写Base URL和本机密钥。密钥保存在 `CUSTOM_PPT_API_KEY`。Base URL使用HTTPS，本机网关允许 `http://127.0.0.1:端口`。这是明确的适配协议，不意味着不同厂商的任意地址均可直接互换；不同接口可通过自建网关转换，或扩展 `ppt-providers.mjs` 的供应商工厂。

通用鉴权：`Authorization: Bearer <本机配置的密钥>`；请求和响应使用JSON。

### 检查连接

`GET {Base URL}/health` → HTTP200，返回JSON，例如 `{"ok":true}`。不创建任务。

### 创建生成任务

`POST {Base URL}/generations`：

```json
{
  "client_request_id": "task_唯一任务编号",
  "prompt_markdown": "已保存的完整课件提示词MD",
  "slides": [{"page_number": 1, "title": "确定整体", "points": ["先明确把什么看作一个整体"]}],
  "visual_plan": {"style": "统一纸艺风格", "pages": [{"page_number": 1, "layout": "图文对照", "visual_prompt": "准确展示整体与部分关系"}]},
  "format": "pptx",
  "aspect_ratio": "16:9",
  "language": "zh-cn",
  "preserve_text": true
}
```

响应：`{"id":"remote_task_123","status":"pending"}`。供应商任务ID只使用字母、数字、下划线和短横线，长度不超过150。建议网关按 `client_request_id` 实现幂等。

### 查询生成结果

`GET {Base URL}/generations/{id}`：

```json
{"status":"pending"}
```

完成：

```json
{
  "status": "completed",
  "download_url": "https://files.example.com/generated.pptx?临时签名",
  "credits": {"deducted": 10, "remaining": 90}
}
```

失败：`{"status":"failed"}`。`running`也可用作等待状态。下载URL应返回真实PPTX，最多50MB；下载不携带网关密钥。工作台只在服务端短暂使用签名URL，不将其写到任务记录或前端。

供应商适配器的代码接口：

```text
create({run_id, brief, plan, markdown}, key, signal) -> {id}
poll(id, key, signal) -> {status, downloadUrl?, credits?}
```

工厂实现见 `plugins/teacher-workbench/ppt-providers.mjs`。新增供应商可以复用下载、恢复、提示词、校验和交付流程，不必重写教师业务。

## 教师工作台本地HTTP接口

Base URL：`http://127.0.0.1:3081/teacher/api`。

| 方法与路径 | 用途 |
|---|---|
| GET `/ppt/settings` | 服务设置及密钥是否已配置，不返回密钥 |
| POST `/ppt/settings` | 保存provider、asset_mode、gamma_engine、quality、theme_id、image_model、style、custom_base_url、auto_generate及可选api_key |
| POST `/ppt/test-connection` | 不创建生成任务的连接检查 |
| POST `/start` | `kind=prompt`只生成提示词；`kind=ppt`生成提示词及PPT；target是已有version_id |
| GET `/jobs` | 查看异步任务进展 |
| GET `/ppt/runs?version=version_id` | 当前教学版本的课件任务、提示词和成品状态 |
| GET `/ppt/file?run=task_id&name=课件提示词.md` | 下载提示词；也支持visual-plan.json、brief.json |
| GET `/ppt/file?run=task_id&name=课件.pptx` | 下载已通过本地校验的图文PPT；也支持交付包.zip |

POST请求使用JSON和本页面 `GET /bootstrap` 返回的 `X-Teacher-Token`，来源须为本机工作台；页面刷新后按最新token调用。创建示例：

```json
{"kind":"prompt","target":"version_已有版本","key":"ui_本次唯一请求编号"}
```

用已保存的提示词生成PPT：

```json
{"kind":"ppt","target":"version_已有版本","key":"ui_新请求编号","source_run_id":"task_提示词任务"}
```

继续已有远端任务：

```json
{"kind":"ppt","target":"version_已有版本","key":"ui_新的本地请求编号","resume_run_id":"task_原课件任务"}
```

相同key和参数返回原任务，不重复执行。创建请求无明确回执时记录为uncertain，不自动重发；先到供应商后台核对再决定是否新建。已有远端ID时复用查询/下载，避免再次创建收费任务。修改服务地址或Gamma模式后不能拿原任务编号到新服务查询。

## 文件与验收边界

提示词及远端回执：`data/ppt-runs/task_id/`。签名下载链接不落盘。

外部课件：`deliverables/lesson_id/vN-version_id/ppt-variants/task_id/`，包含替换为图文PPT的整套材料、提示词、视觉方案、生成记录、哈希清单和ZIP。原始交付目录中的文件保留。

程序核对文件、正文与页码，不能证明图片的事实正确性、Office实际渲染、适龄性或课堂效果。图片内部文字仍可能不是可编辑对象；正文必须可提取，否则拒绝作为已校验交付。

## 本地修复正文（2026-11-05）

Gamma即使使用preserve，也可能把一句话拆成多张卡片、调整标题位置或漏掉要点。逐页文字校验失败时，保留 `download.pptx` 原文件，使用已保存教学设计在本地恢复原生可编辑正文、重排受影响页面、恢复逐字稿备注，再执行原有完整校验，不新增Gamma生成请求。新Gamma任务自动尝试；已有失败任务可点击“本地修复正文并校验”。

识别出的基础几何页面使用可编辑点、线和坐标网格；坐标从原页面或逐字稿提取，顶点移动从既有坐标计算。此能力只覆盖明确识别的几类图示，不是任意数学图形的自动纠错。其他插图仍须教师核对。页数、比例或整页缺少原生文字等问题不会绕过验收。

```json
{"kind":"repair_ppt","target":"version_已有版本","key":"ui_新请求编号","resume_run_id":"task_已下载课件任务"}
```

该任务不使用模型或PPT服务密钥，不发送外部API请求。交付包的 `课件生成记录.json` 记录原文件哈希、恢复页码、原文字差异和图示数据，原Gamma文件及基础交付物保留不变。文件校验通过仍不等于教师教学审阅通过。

## 教师统一交付入口（2026-11-06）

教师只使用「创建课程并生成整套材料」或学情优化入口。所选PPT服务自动执行，旧配置中的auto_generate=false不再把教师流程停在提示词阶段。页面移除独立提示词与图文课件板块，所有版本统一展示一份最终PPT及配套DOCX和整套下载。

- GET `/delivery?version=version_id`：整套材料状态（generating / ready / needs_setup / incomplete / not_started）、阶段与最终页数；只有最终课件完成并校验后为ready。
- GET `/file?version=version_id&name=课件.pptx`：统一下载最终课件。其他教师材料使用同一路径；最终课件未完成时返回409，避免把基础稿当成成品。
- POST `/start`，`{"kind":"complete","target":"version_id","key":"本次请求唯一ID"}`：从已保存版本继续完成整套材料。宿主自动选择复用已校验结果、本地修复下载文件、继续已有远端任务或完成尚未开始的课件制作。同一种请求传 `restart:true` 时不复用旧远端任务、候选文件或创建结果，只复用已保存的课件提示词并新建供应商任务，用于供应商明确失败、创建结果不确定或服务模式/地址已变更的情况；该操作可能产生新费用，教师页面会二次确认。

教师交付包位于每版的 `teaching-bundles/<run_id或native>/`，只包含一份 `课件.pptx`、教案、逐字稿、检测题、教学设计、信息密度、修改说明、文本内容预览及清单。ZIP内的课件与单独下载的课件哈希相同。提示词、视觉方案、供应商记录和原候选稿继续保存在后台目录，兼容原工程接口；教师页面与教师交付ZIP均不展示这些中间产物。原基础稿和历史交付物不覆盖。

## 教材素材与本地图文合成（2026-11-07）

知识库导入 PDF/DOCX 时会拆出教材插图：PDF 用 PyMuPDF 读取页面图片区域并渲染为 PNG，DOCX 读取 `word/media` 中较大的图片；素材保存在 `data/knowledge/<id>/assets/`，`assets.json` 记录页码、尺寸、哈希、邻近文字（图注）和页面上下文。素材图注是确定的邻近文字，不是视觉模型生成的语义描述。

视觉方案的每页新增可选 `asset_ids`（最多 2 个），只能引用本课知识库拆出的素材。视觉 Agent 根据页面文字和素材图注选择；如果模型没有选择，宿主会按页面文字与素材上下文的词面重合度做一次保守兜底。

`asset_mode` 控制最终课件来源：

- `auto`：本课有教材素材时本地合成教材插图课件；没有素材时使用所选 PPT 服务。
- `provider`：使用所选 PPT 服务（Gamma/自定义网关），教材素材不进入 Gamma；自定义网关会在请求中收到 `assets` 元数据。
- `local`：只用本地课件，使用教材素材和可编辑图形。

本地合成由 `ppt-compose-local` 执行：用 `python-pptx` 在页面右侧放入所选教材图片，保留原生可编辑文字，正文逐页校验，图片和所有对象不得超出页面；输出到 `deliverables/<lesson>/vN-<version>/local-variants/<run>/`，写 `result.json` 后由教师统一交付包使用。它不调用外部 PPT 服务，也不覆盖基础稿。

自定义网关创建请求新增可选字段：

```json
{"assets":[{"id":"asset_xxx_p3_1","knowledge_id":"knowledge_xxx","page":3,"file":"page-03-01.png","caption":"钟面与秒针"}]}
```

网关需要自行读取项目本机的素材文件或转成可访问的图片；Gamma 公共接口当前仅按提示词生成 AI 配图，不接收项目里的教材原图。要使用教材原图，选择 `auto` 或 `local`；要 AI 配图，选择 `provider`。两种图目前不会自动混合到同一份课件。

知识库素材接口：

- GET `/knowledge/assets?knowledge=id`：素材元数据，不返回本机绝对路径。
- GET `/knowledge/asset?knowledge=id&name=page-03-01.png`：返回图片；文件名和目录经过校验。
- POST `/knowledge/refresh`：对已有知识库重新拆分素材（用于升级前导入的条目）。

图片抽取、图注关联和版式仍需教师核对；程序验证的是文件、页码、正文和页面边界，不验证插图事实、版权或教学适用性。

## 视觉描述、图注与手动选图（2026-11-07）

- `GET /vision/settings`：返回视觉模型、Base URL、是否启用以及密钥是否已配置；不返回密钥。
- `POST /vision/settings`：保存 `enabled`、`base_url`、`model` 和可选 `api_key`；密钥写入 DSH 凭据 `SILICONFLOW_API_KEY`，配置文件只保存模型和地址。
- `POST /knowledge/describe`，`{"id":"knowledge_id","limit":6}`：对没有描述的素材调用视觉模型，生成 2—4 句客观描述并写回 `assets.json`；默认每批最多 6 张，可重复调用。当前支持 SiliconFlow 的 `Qwen/Qwen3-VL-30B-A3B-Thinking`，请求走 OpenAI 兼容 `POST /v1/chat/completions`，图片使用 `image_url` data URL。
- `POST /knowledge/asset/update`，`{"knowledge":"knowledge_id","asset":"asset_id","caption":"...","description":"..."}`：教师手动修改图注或描述。
- `GET /knowledge/text?knowledge=id`：返回抽取的完整文本（最多 20 万字）和字数、分段数。
- `GET /ppt/pages?version=version_id`：返回当前版本分页后的页面（页码、标题、要点、环节）和本课知识库素材（含图注、描述）。
- `POST /start`，`{"kind":"local_ppt","target":"version_id","key":"...","assets_by_page":{"3":["asset_id"]}}`：教师手动指定每页素材，宿主创建 brief、保存视觉方案、调用 `ppt-compose-local` 重新合成教材图文课件并生成教师交付包；不调用模型或外部 PPT 服务。
- 视觉方案中的 `asset_ids` 可以来自模型选择、词面兜底或教师手动选择；手动 `local_ppt` 任务会生成独立 run 和 `local-variants/<run>/`，旧交付物保留。

图片描述是视觉模型生成的辅助信息，可能误读图中文字、数量或关系；图注和描述都可由教师修改，正式上课前仍需对照原教材。

## 情景图、图表与教材留空题（2026-11-07）

- 图片素材会附带 `kind`：`scene`（情景导入，通常含人物/对话框）、`diagram`（图表/示意）、`question`（教材留空题）。`dialogue` 保存图片附近的短文字块（对话框/场景文字）；`question_text` 保存留空题的教材原文。
- 留空题从页面文字块中识别含空括号 `（ ）` / `( )` 或连续下划线的区域，按原区域渲染为 PNG；它不经过视觉描述，使用教材原文。它只覆盖这种排版的题目，纯图片题或复杂表格题可能漏检。
- 素材接口返回 `kind`、`dialogue`、`question_text`；图注/对话/描述可通过 `POST /knowledge/asset/update` 修改。素材画廊和 `GET /ppt/pages` 支持按类型筛选/分组。
- `local_ppt` 合成时：`scene`/`diagram` 放在页面右侧，`question` 放在页面底部原题区域（最多 2 张），正文保持原生可编辑；留空题是原题截图，不是可编辑题干。没有空括号的题目、纯图片题或复杂表格题需要教师手动补图或后续增加文本型题目资产。
- 情景图裁剪包含图片区域内的对话框；独立文本框若在图片区域外，对话文字保存在 `dialogue` 中，但不会自动出现在截图里。教师可在知识库查看和修改对话/图注。
