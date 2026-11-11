# rt-full-release-7835

Claude Code 插件：**RT 全量发版套餐自动排车**。读取飞书安全员表和 RT 调度弹窗，按城市与资源池生成排车方案，确认后回填车辆与下发时间；跨城往返组只排车、不下发。

## 安装

```
/plugin marketplace add dingchengzhang7-ai/rt-full-release-7835
/plugin install quanliangfaban7835skill@rt-full-release-7835
```

装好后在 Claude Code 里说「给这个 RT 任务排 7835 发版」，或直接跑命令：

```bash
cd ~/.claude/plugins/*/rt-full-release-7835/skills/quanliangfaban7835skill
python3 rt_sched.py check
python3 rt_sched.py dump
python3 rt_sched.py plan      # 到这里停下，贴出方案等确认
python3 rt_sched.py fill --yes
python3 rt_sched.py fill --yes --dispatch --confirm-dispatch
```

## 首次配置

| 凭据 | 做法 |
|---|---|
| RT 登录态 | 浏览器登录 RT，导出 Playwright `storage_state` 到 `~/.claude/feishu/auth.json` |
| 飞书 cookie | 浏览器 F12 → 任意请求 → Copy as cURL，取 `cookie` 存到 `~/.claude/feishu/cookie.txt` |
| 任务与表 ID | `cp config.example.json config.json`，填 `task_url`、`feishu.app_token`、`feishu.table_id` |
| RT token | 复用 `~/.claude/skills/rt-base-board/config.json` 里的 `rt_token`（仅 `check` 用） |

详细说明见 [skills/quanliangfaban7835skill/README.md](skills/quanliangfaban7835skill/README.md)。

## 安全边界

- 默认只出方案，不动线上；`fill` 需要 `--yes`，最终「调度」还要 `--confirm-dispatch`
- 凭证、车辆清单、运行截图都不入库

## License

MIT
