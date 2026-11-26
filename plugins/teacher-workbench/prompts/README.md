# 提示词维护说明

`system.md` 是教师 Agent 的主系统提示词。DSH persona 的 prefix 只接受字符串，因此使用构建脚本把此文件编译进 `../cordis.patch.yml` 的 teacher preset；complete=true，使它作为该 Agent 的系统提示词。它同时用于教学设计、学情修订和逐页视觉规划会话。

阶段规则按职责分开维护：

| 文件 | 调用阶段 | 实际消息位置 |
| --- | --- | --- |
| system.md | 所有 teacher preset 会话 | DSH persona，system 层 |
| design.md | 首次教学设计 | 任务消息，后附动态 schema 与本课输入 |
| revision.md | 教师确认后的修订 | 任务消息，后附动态 schema、原设计、反馈与教师选择 |
| visual.md | 页面布局、教材选图、生图规划 | 任务消息，后附逐页 brief、素材目录、生图开关与限额 |
| vision.md | Qwen-VL 图片观察与描述 | 图像描述 API 的 system 消息；图片及图注单独放 user 消息 |
| context.md | 教学与视觉阶段 | 说明筛选、来源定位、历史证据和省略范围的使用方式 |
| quality.md | 首次设计与修订阶段 | 提交前检查目标、过程、检测、事实和材料一致性 |

图片生成模型使用视觉 Agent 提交的 image_prompt，以及宿主追加的年级、风格和图片约束；本次没有改变生图缓存、计费恢复或服务配置。

在项目根目录执行：

```powershell
python tools/build_teacher_system_prompt.py
python tools/build_teacher_system_prompt.py --check
```

修改 system.md 后执行构建并重启工作台，新会话使用新系统提示词。修改阶段 Markdown 后，新任务直接读取；已保存的教学版本、视觉方案和图片不会被重新生成。

动态 JSON Schema 与程序校验仍是结构、引用和授权检查的依据。提示词描述期望行为，不代替程序校验，也不能保证模型每次输出均正确。首次、修订与视觉阶段各自使用明确的提交工具；工具保存成功和宿主最终文件交付保持分开。

调试参数在「模型设置 → 高级：生成参数与上下文」，配置文件为 `config/agent-tuning.json`。新任务保存配置快照；`GET /teacher/api/context-report?job=<任务ID>` 可检查生效参数、预算和选择数量。详情见项目 `docs/备课助手上下文与调参接口.md`。
