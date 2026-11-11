# 全量发版 skill —— RT 发版套餐自动排车

把「一个发版套餐 81 条路线分给几十台车」从手工点点点，变成一条命令出方案、确认后一键回填。

- 适用对象：RT 路测任务里的 **HNP 发版套餐**（当前配置为 7835km 那个）
- 车辆来源：飞书安全员表，每次运行现读，不用维护本地名单
- 默认行为：**只出方案，不动线上**；最终「调度」必须二次确认

---

## 它解决什么问题

一份发版套餐 81 条路线，分散在芜湖、广州、上海、苏州、北京、深圳、杭州、武汉&南昌、重庆&成都九个城市。
人工排车的痛点是固定的：

| 痛点 | 具体表现 |
|---|---|
| 往返路线必须同车 | 深圳机场、金海园⇄创新港、公司⇄广汽研究院等 6 组路线拆到两辆车上就白跑 |
| 上海要分段 | 创新港和金海园是两个资源池段，`Shanghai_Taunus_HWS_001/002` 必须落创新港的车 |
| 界面会动态过滤 | 已配置到其他套餐的、已经跑过的路线会被隐藏，**剩多少条每次都不一样** |
| 弹窗没有保存 | 底部只有「取消」和「调度」，关掉浏览器等于白填 |
| 填错要重来 | 81 行逐行选车，手工极易漏行、错行 |

这个 skill 把这些规则固化下来，并且把「排」和「发」拆成两道人工闸门。

---

## 能力

1. **现读车辆**：直接走飞书多维表格接口拉安全员表，解析安全员姓名、城市、调度状态，只取「可调度」的车。
2. **进页面取路线**：自动打开任务详情 → 调度套餐弹窗 → 选资源池 `HNP-组内组间` → 打开「过滤同产品已配置路线」→ 分页切 100 条/页 → 抓出本次真正可排的路线。
3. **去重对账**：拿套餐全量 81 条和界面剩余条数做差，把被过滤的路线逐条列出来；如果往返组整组被过滤，明确提示本次不排。
4. **自动排车**：按城市分资源池段，往返组整组落同一辆车并设下发时间 `now`，其余路线避开往返车、同城轮转摊匀、下发时间留空。
5. **产出方案**：同时生成 Markdown（贴给同事看）、CSV（Excel 打开）、JSON（机器可读）。
6. **确认后回填**：逐行选车 + 设时间，任何一行失败都会记录，不会静默跳过。
7. **最终调度**：回填完成后先自检车辆数和时间数，全部对上才允许点「调度」。

---

## 安全边界

- **默认只出方案**。跑完 `dump` + `plan` 就停下等你确认，不会自动进下一步。
- `fill` 必须带 `--yes`，不带直接拒绝执行。
- 最终「调度」必须同时带 `--dispatch --confirm-dispatch`，少一个都不点。
- 回填不完整（车辆少了、时间少了、有失败行）时，脚本主动拒绝点调度。
- 只使用安全员表里「可调度」的车，不会去占用已派车的车辆。
- 不补齐被平台过滤的路线——那些归其他套餐或已经跑过。

---

## 安装

```
/plugin marketplace add dingchengzhang7-ai/rt-full-release-7835
/plugin install quanliangfaban7835skill@rt-full-release-7835
```

不想用插件，也可以直接克隆后拷进技能目录：

```bash
git clone https://github.com/dingchengzhang7-ai/rt-full-release-7835
cp -r rt-full-release-7835/skills/quanliangfaban7835skill ~/.claude/skills/
cd ~/.claude/skills/quanliangfaban7835skill
cp config.example.json config.json    # 再改 task_url / feishu 字段
```

---

## 快速开始

```bash
cd ~/.claude/skills/quanliangfaban7835skill

python3 rt_sched.py check      # 1. 体检：任务活着吗、套餐在不在
python3 rt_sched.py dump       # 2. 进弹窗抓本次可排路线
python3 rt_sched.py plan       # 3. 现读车辆 + 生成方案（到此为止，等确认）
#    ↑ 把输出贴出来确认

python3 rt_sched.py fill --yes                            # 4. 只回填，不调度
python3 rt_sched.py fill --yes --dispatch --confirm-dispatch  # 5. 回填并最终调度
```

第 4、5 步必须在**同一次会话**里连着跑完——调度弹窗没有保存按钮，填完关掉就没了。

想先试水可以只回填前几行：

```bash
python3 rt_sched.py fill --yes --limit 5
```

---

## 输出长什么样

```
去重对账：套餐全量 81 条 → 本次可排 71 条，平台过滤 10 条（已配置到其他套餐或已经跑过，属正常）
  上海发版套餐(HNP_1000km)：2 条
    - Shanghai_POI_HUU_360-1
    - Shanghai_POI_HUU_359-2
  ...

车辆清单：刚从安全员表现读，42 台
路线 71 条 | 往返组 23 条

### 往返组（同一辆车 + 下发时间 now）
   5.4 芜湖⇄南京
     京AAD6889 LC6-EAW6079 张安全 — 6 条
     Wuhu_WHUAS_CMA_HWG_003 / Wuhu_WHUAS_CMA_EXG_004 / ...

### 深圳  10 条
   粤BES5707   LC6-BCW2831    陈伟文    3 条
   粤BHS9727   HC2-BHS9727    高泽华    3 条
   ...

明细：state/assign.md / assign.csv / assign.json
```

明细文件在 `state/` 下：

| 文件 | 用途 |
|---|---|
| `assign.md` | 表格，直接贴到群里或文档 |
| `assign.csv` | Excel 打开、二次编辑 |
| `assign.json` | 回填脚本读取；想换车直接改这里的 `host` / `plate` |
| `fill_result.json` | 每行回填的成功/失败记录 |
| `filled.png` | 回填后的页面截图 |

---

## 排车规则

1. **往返组整组同车**：`config.json → round_trip_groups` 里的 5.1–5.6 组，组内所有路线给同一辆车，**这 23 条关联路线的下发时间全部点 `now`**。
   用户明确说某几组不下发时，重跑 `plan --no-now 5.4,5.5`（或 `--no-now all`），那几组只排车、下发时间留空。
2. **往返车不再分其他路线**：其余路线从该城市第二辆车开始轮转，**下发时间一律不点**。
3. **往返车怎么选**：自营车队（Hostname 以 `LC6-` / `HC2-` / `QZH-` 开头）优先，再按车牌升序——规则确定，每次可复现。不满意可直接改 `state/assign.json`。
4. **上海拆两段**：`shanghai_chuangxin_routes` 里的路线归创新港，其余归金海园；`HWS_001/002` 在创新港集合内，所以必然排到创新港的车。
5. **去重靠平台开关**：不手工去重，打开「过滤同产品已配置路线」即可；脚本会校验开关状态并做差集核对。
6. **数量不写死**：81 是套餐全量，界面剩多少取决于任务状态（跑过的、别处配过的都会消失），脚本按实际条数排。

---

## 依赖

| 依赖 | 说明 | 失效怎么办 |
|---|---|---|
| `auth.json` | RT 的 Playwright 登录态，默认 `~/.claude/feishu/auth.json` | 浏览器登录一次，重新导出 storage_state |
| 飞书 cookie | `~/.claude/feishu/cookie.txt`，读安全员表用 | 浏览器 F12 复制最新 cookie 覆盖 |
| 安全员表 | 飞书 wiki `Bmt7w8ikEiTryskEjG8cKIcrneJ` 内嵌多维表格 | 表结构变了改 `config.json → feishu` |
| RT token | 复用 `~/.claude/skills/rt-base-board/config.json` | 仅 `check` 用，过期重新取 |

---

## 目录结构

```
quanliangfaban7835skill/
├── SKILL.md                 技能说明（含完整工作流程与页面踩坑记录）
├── config.json              本地配置（不入库，从 config.example.json 复制）
├── config.example.json      配置模板
├── package_routes.json      套餐全量 81 条路线（对账用）
├── rt_sched.py              主程序：check / dump / vehicles / plan / fill
├── feishu_vehicles.py       飞书安全员表现读
├── vehicles.json            最近一次现读的车辆（自动覆盖）
├── vehicles.prev.json       上一次的车辆，便于对比
└── state/
    ├── routes_visible.json  本次界面可排路线
    ├── assign.{md,csv,json} 排车方案
    ├── fill_result.json     回填结果
    └── filled.png           回填截图
```

---

## 常见问题

**Q：为什么套餐有 81 条，界面只有 71 条？**
A：平台过滤掉了「已配置到其他 HNP 套餐」和「已经跑过」的路线。跑掉 20 条就只剩 61 条，每次不同，属正常。`plan` 会把差集列出来。

**Q：往返组某条路线不见了呢？**
A：说明它已经被跑过或被其他套餐占用，本次不需要排。如果整组消失，脚本会提示「该组本次不排」，不要硬塞到别的车上。

**Q：想让某条路线换辆车？**
A：改 `state/assign.json` 里对应行的 `host` / `plate` 再跑 `fill`，不用重跑前面的步骤。

**Q：填到一半浏览器崩了怎么办？**
A：线上没有任何变化（没点调度），重跑 `fill` 即可。

**Q：任务显示「已取消」还能排吗？**
A：先恢复任务，否则套餐会被清掉，`check` 也会提示。
