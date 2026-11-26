# 在原始 DSH 中使用 WorkBuddy

照常双击 `C:\dsh\打开原始DSH.vbs`。新会话默认使用 **WorkBuddy → 快速**，已验证能够返回回答。

在输入框的模型菜单中可切换 WorkBuddy 的其他可用模型。已有会话可能保留原先的模型选择；需要时在当前会话切换，或新建会话。

账号、模型和积分管理入口：左下角「更多」→「设置」→「WorkBuddy」。

本次接入使用本机已有的 WorkBuddy 登录状态。日常不需要保持 WorkBuddy 窗口打开；DSH 启动时会自动启动本地代理。登录失效时，可在 WorkBuddy 插件的账号页重新登录。

调用过程：DSH → workbuddy-to-dsh 插件与本地代理 → WorkBuddy 云端模型。实际可用模型、积分消耗和限流由 WorkBuddy 账号决定。

安装位置为 `C:\dsh\harness-home\profiles\original`，插件为 `workbuddy-to-dsh@1.24.2`。插件原项目：<https://github.com/Ianzhyh/workbuddy-to-dsh>。这是第三方接入方式，上游变更时可能需要更新。

安装前的配置和启动/停止脚本备份位于 `C:\dsh\data\backups\workbuddy-20261117-221604`。本次还修正了原始启动器向插件传递的配置路径，并修正了原启动/停止脚本供 Windows PowerShell 使用的 UTF-8 编码标记。
