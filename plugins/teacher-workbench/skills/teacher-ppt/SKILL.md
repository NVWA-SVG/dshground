---
name: teacher-ppt
description: 为已保存的教学设计规划逐页布局、选择教材原图和按需生图，由宿主装配并校验一份可编辑教学课件。
---

本技能由教师工作台在教学设计导出后加载。完整阶段规则维护在 `../../prompts/visual.md`，宿主会把它与本技能一起注入当前任务，不需要额外文件读取工具。

输入是固定页序和原文的 brief、本课素材目录、动态 JSON Schema，以及本次生图开关与张数上限。

职责：为每页确定可执行布局，选择真实 asset_ids，按教学需要填写 image_prompt 与 image_reason。教材原图和 AI 插画可同页混用；不要重绘原题，不改正文、页数或目标，不假称生成了成品。

新版方案还要逐页提交 teaching_goal、student_task、learning_focus、asset_reason、density_note。精确图解只选择本页 diagram_candidates 的 diagram_id，并使用允许的 diagram_stage；数值、进退位和坐标由本地程序计算。原生图解页不叠加原图或生图。原文较长时优先保证正文可读，不能依赖缩到很小的字来塞入图片。

提交：调用 teacher_save_ppt_prompt，使用 {"plan": 完整对象}，不可提交空参数或 JSON 字符串。校验失败后按错误修正，ok=true 后结束。

保存后由宿主负责按当前配置生图、缓存恢复、本地装配、逐页校验和统一交付。教材图片可保持原图，标题和正文保持原生文字。密钥、模型供应商、付费重试和最终下载状态由宿主管理。
