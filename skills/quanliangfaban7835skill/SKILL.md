---
name: quanliangfaban7835skill
description: 给 RT 路测任务排「发版套餐」的调度：从安全员表取可调度车辆，进 RT 调度弹窗选资源池 HNP-组内组间、打开「过滤同产品已配置路线」，把每一条路线指派到具体车辆（Hostname），并把必须同车的往返路线排给同一辆、下发时间设为 now。默认只输出排车方案，经用户确认后才回填页面，且永不点击「调度」。当用户说"排调度/排车/开始调度/把 7835 套餐排一下/发版套餐分配车辆/往返路线排同一辆车"时触发。
---

# 全量发版 skill（quanliangfaban7835skill）

RT 发版套餐 → 排车方案 → （确认后）回填页面。

## 铁律

1. **默认只出方案**：跑完 `dump` + `plan` 就把表贴给用户，**停下来等确认**。
2. `fill` 必须用户明确同意后才跑，且必须带 `--yes`。
3. **最终「调度」默认不点**。只有用户明确说"可以调度/确认下发"时，才追加 `--dispatch --confirm-dispatch`。
4. **调度必须在同一次会话里完成**：弹窗底部只有「取消」和「调度」，**没有保存**，关掉浏览器表单就没了。所以不能"今天填好、明天再下发"，`fill` 与 `--dispatch` 必须一次跑完。
5. 调度前脚本会自检：车辆和时间没全部填成功就拒绝点调度。

## 工作流程

```
触发（"排一下 7835 发版套餐"）
  │
  ├─① check     只读体检：任务是否还活着、7835 套餐在不在任务上
  │     └─ 任务已取消 / 套餐不在 → 先编辑任务添加套餐，再继续
  │
  ├─② dump      浏览器打开任务 → 调度套餐弹窗
  │     ├─ 资源池选「HNP-组内组间」
  │     ├─ 打开「过滤同产品已配置路线」（每次都要开，这是去重来源）
  │     ├─ 分页切到 100 / page
  │     └─ 导出界面真实出现的路线 → state/routes_visible.json（每次条数不同，见 ③）
  │
  ├─③ 去重对账  自动：套餐全量 81 条 vs 界面剩余 N 条（N 每次不同）
  │     ├─ 打印被过滤的 81-N 条：已配置到其他套餐 / 已经跑过
  │     └─ 若往返组被过滤掉，明确告警「该组本次不排」
  │     └─ 若界面出现重名 → 报警「过滤开关没开」，停下重 dump
  │
  ├─④ 现读车辆  自动：飞书安全员表 /space/api/v1/bitable → vehicles.json
  │     └─ cookie 失效会直接报错 → 更新 ~/.claude/feishu/cookie.txt
  │
  ├─⑤ plan      生成方案
  │     ├─ 上海拆创新港 / 金海园；HWS_001/002 必须落创新港车
  │     ├─ 往返组（5.1–5.6）整组同一辆车，下发时间 = now
  │     ├─ 其余路线避开往返车，同城轮转，下发时间留空
  │     └─ 输出到 state/assign.{md,csv,json}
  │
  ├─⑥ 停下来 ── 把方案贴给用户，等明确同意（默认终点）
  │
  ├─⑦ fill --yes
  │     └─ 同一浏览器会话里回填车辆 + now 时间；不点调度，表单随时可弃
  │
  └─⑧ fill --yes --dispatch --confirm-dispatch
        ├─ 只有用户明确说"可以调度 / 确认下发"才执行
        └─ 先自检「车辆数 = 本次路线数」「下发时间数 = 本次往返组条数」，缺一条就拒绝点击
```

关键分界：**⑥ 之前全是只读**，⑦⑧ 才写线上。⑦ 填完不点调度，等于白填（弹窗没有保存），所以 ⑦⑧ 必须连着跑完。

### ① check —— 只读体检

```bash
python3 rt_sched.py check
```

- 走 REST，不启浏览器，约 2 秒：`GET /dlp/rt-manager/{taskId}` 拿任务名和状态，`GET .../routes_beautify` 拿任务上所有套餐。
- 要确认两件事：**任务状态不是「已取消」**；**套餐列表里有名字含 `7835` 的那一个**。
- 输出会列出每个套餐的状态、`closed`、路线条数，最后一行给出「目标套餐: 在任务上 / 不在任务上」。
- 处理分支：套餐不在 → 任务页 `编辑 → 添加路测套餐`，产品选 HNP、名称输 `7835`，保存后再跑 ②；任务已取消 → 先恢复任务，否则套餐加了也会被清掉。

### ② dump —— 把界面上真实能排的路线抓下来

```bash
python3 rt_sched.py dump
```

需要 `~/.claude/feishu/auth.json`（RT 的 Playwright storage_state）。一次完整流程约 1 分钟，大部分时间在等 SPA 和表格渲染。脚本按顺序做：

1. 打开任务详情页，等页面骨架出现。
2. 点「调度套餐」打开模态框（`.ant-modal` 里含「调度套餐」且可见的那个）。
3. 资源池下拉选 **HNP-组内组间**。
4. 打开 **「过滤同产品已配置路线」** switch，并校验它确实是开的状态——这是唯一的去重点，缺它就会排进重复路线。
5. 分页从默认 30 条/页切到 **100 / page**（选项是英文 `"100 / page"`），保证一页抓全。
6. 遍历表格行，取序号、路线名称、城市、圈数。**路线名称读的是 `td:nth-child(2) input.value`**，不是文本节点；城市/圈数同理。
7. 写入 `state/routes_visible.json` 并逐条打印。

判定：**没有固定条数**。套餐全量 81 条，平台会按「已配置到其他套餐」「已经跑过」过滤，剩 61 / 71 / 80 都正常。唯一必须重来的是：过滤开关显示关闭、或界面出现重名（说明没去重）。登录过期会直接报错，在浏览器里重新登录一次再导出 `auth.json`。

### ③ 去重对账 —— 81 减去平台过滤后还剩多少

`plan` 第一步自动跑，不需要单独命令。

- 数据源：`package_routes.json`（套餐定义的全量 81 条）+ `state/routes_visible.json`（界面本次剩下的 N 条）。
- 打印每个模板下被过滤掉的路线，例如 `上海发版套餐(HNP_1000km)：2 条 - Shanghai_POI_HUU_360-1 / 359-2`。
- 同时检查界面里有没有重名。有重名 = 过滤开关没开或页面没刷新，**停下重新 `dump`**。
- 被过滤掉的是两类：已配置到任务上其他 HNP 套餐的、以及已经跑过的（跑 20 条就少 20 条）。两类都**不该排、也不要手动补全**。
- 如果某个往返组整组被过滤，`plan` 会告警「该组本次不排」，此时不要硬塞到别的车上。

### ④ 现读车辆 —— 每次都重新读安全员表

`plan` 默认自动执行（单独跑用 `python3 rt_sched.py vehicles`），约 2 秒。

- 数据源：飞书文档内嵌的多维表格（`app_token` / `table_id` 在 `config.json → feishu`）。
- 先 `clientvars` 拿 `fieldMap`，再 `records` 拿 `recordMap`；`data.records` 是 **base64(gzip(JSON))**，要解压。
- 取 9 个字段：车牌、Hostname、安全员、车型、当前使用、城市、调度状态、测试内容、备注。
- 安全员是 user 类型字段，形如 `{'users': [{'name': '张三-138xxxx0000'}]}`，取姓名并去掉尾部手机号。
- 排序：上海（创新港）、上海（金海园）排前，然后按城市、可调度优先、车牌升序。
- 写 `vehicles.json`，旧的留一份 `vehicles.prev.json` 便于对比。约 42 台。
- 只用 `调度状态 == 可调度` 的车。cookie 过期会明确报错，更新 `~/.claude/feishu/cookie.txt` 即可。

### ⑤ plan —— 生成排车方案

```bash
python3 rt_sched.py plan              # 自动现读车辆
python3 rt_sched.py plan --no-refresh # 用本地车辆缓存，快速重算
python3 rt_sched.py plan --no-now 5.4,5.5 # 这几组只排车不下发（默认全部 now 下发）
```

1. 载入 `routes_visible.json`，跑去重对账。
2. 现读车辆，筛出可调度的。
3. 把路线分到资源池段：上海按 `shanghai_chuangxin_routes` 拆成 **创新港 / 金海园**，其余城市走 `city_to_segment`（芜湖→芜湖&合肥&南京，武汉/南昌→武汉&南昌，重庆/成都→重庆&成都…）。城市没映射会直接报错要求补配置。
4. 每段取本段可调度车辆，**自营车队（`LC6-` / `HC2-` / `QZH-` 开头）优先，再按车牌升序**——保证往返组每次落在同一辆车，可复现。
5. 往返组内所有路线整组给第一辆车，**默认下发时间 `now`**；其余路线从第二辆车开始轮转摊匀，下发时间留空。
   仅当用户明确说「不下发/先不排/不要 now」时，才用 `--no-now 5.4,5.5`（或 `--no-now all`）把那几组的下发时间留空。
6. 写 `state/assign.json`（机器可读）、`assign.csv`（Excel）、`assign.md`（表格），然后打印。

输出分三块：**往返组汇总**（组名、车、司机、条数、路线清单）→ **各城市车辆负载**（每辆车几条）→ **文件路径**。最后提示下一步的两个选项。

### ⑥ 停下来 —— 人工确认门

把上面的方案原文贴给用户，重点讲三件事：往返组落在哪辆车上、每个城市各用了几辆车、被过滤掉多少条（以及是否含往返组）。然后**停**。

用户如果觉得某辆车不合适，直接改 `state/assign.json` 里的 `host` / `plate` / `dispatch` 再跑 ⑦，不必重跑 ①②。

### ⑦ fill —— 回填表单（不点调度）

```bash
python3 rt_sched.py fill --yes            # 全量
python3 rt_sched.py fill --yes --limit 5  # 先试跑 5 行
```

- 必须用户明确同意后才执行，且必须带 `--yes`；不带会直接拒绝。
- 新开浏览器进弹窗，重复 ② 的池选择 + 过滤开关 + 100/页，然后按 `assign.json` 逐行处理：
  - 按 `input.value` 找到路线行 → 点编辑；
  - 车辆下拉按城市过滤，**精确匹配 Hostname**；
  - 需要 `now` 的行填下发时间：聚焦 input → 合成 mousedown/mouseup/click → 轮询等 `.ant-picker-dropdown` 出现 → 点 footer `Now` → 点 `OK`；
  - 单行失败会记录下来继续，不中断整批。
- 写 `state/fill_result.json` 和 `state/filled.png`（截图留证）。
- **不点「调度」**，所以这一步线上没有任何变化，随时可以关掉重来。

### ⑧ dispatch —— 最终下发

```bash
python3 rt_sched.py fill --yes --dispatch --confirm-dispatch
```

- 只有用户明确说"可以调度 / 确认下发"才执行；缺 `--confirm-dispatch` 会被拒绝。
- 与 ⑦ 是同一段代码、同一次会话：回填完成后先自检 **车辆数 = 本次路线数、下发时间数 = 本次往返组条数、无失败行**，任何一项不满足就打印「回填不完整，拒绝点调度」并退出，不点按钮。
- 自检通过才找 footer 里文本为「调度」的按钮点下去，然后截图保存结果。
- 点了就不可撤销，线上调度立即生效。

### 常见中断与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `dump` 打不开弹窗 | auth 失效 / 页面改版 | 重新登录导出 `auth.json`；页面改版则先核对弹窗选择器 |
| 抓到 81 条（= 全量） | 过滤开关没打开，或确实还没跑过任何路线 | 先看开关状态；开关是开的就说明本次确实一条都没被过滤 |
| 车辆只有个位数 | 飞书 cookie 过期 | 更新 `~/.claude/feishu/cookie.txt` |
| 某城市「没有可调度车辆」 | 安全员表里该城市车都被占用 | 在表里释放车辆，或调整 `city_to_segment` |
| 填到一半浏览器崩了 | — | 线上没变化（未点调度），重跑 ⑦⑧ |

## 用法

```bash
cd ~/.claude/skills/quanliangfaban7835skill

# 0) 只读体检：任务状态 + 套餐是否还在
python3 rt_sched.py check

# 0.5) 现读安全员表（plan 默认也会自动刷新，此命令可单独跑）
python3 rt_sched.py vehicles

# 1) 打开调度弹窗，选资源池/开过滤/切 100 条每页，导出可排路线
python3 rt_sched.py dump

# 2) 生成排车方案并打印（会先去飞书现读车辆；--no-refresh 则用本地缓存）
python3 rt_sched.py plan

# 3) 用户确认后：只回填，不下发
python3 rt_sched.py fill --yes

# 4) 用户确认调度后：回填并点最终「调度」（必须同一次跑完）
python3 rt_sched.py fill --yes --dispatch --confirm-dispatch
python3 rt_sched.py fill --yes --limit 5    # 试跑前 5 行
```

产物在 `state/`：`routes_visible.json`、`assign.{md,csv,json}`、`fill_result.json`、`filled.png`。

## 排车规则

- **往返路线必须同一辆车**，且这些车的下发时间选 `now`（说明书 5.1–5.6 组，见 `config.json → round_trip_groups`）。
- **去重是每次必做的一步**：排之前先确认「过滤同产品已配置路线」已开，`plan` 会把「81 → 本次 N 条」的差集打印出来复核。**N 每次都不一样**：被其他套餐配置过的、已经跑过的都会被过滤掉，跑 20 条就只剩 61 条。只排界面里真实出现的那些，不要去补齐被过滤的路线。
- **其余路线不再分给已经排了往返组的车**，只分给同城市的其他可调度车辆，轮转摊匀；下发时间**留空**。
- 上海必须拆成「创新港」「金海园」两段：`Shanghai_Taunus_HWS_001/002` 是金海园⇄创新港通勤，要排在**创新港**的车。
- 车辆一律用 **Hostname**（调度下拉里显示的是 hostname，不是车牌）。
- **往返组落在哪辆车上** = 该城市可调度车辆里，先排除供应商车（Hostname 不以 `LC6-`/`HC2-`/`QZH-` 开头，如 `SG-HL-3805`），再按车牌升序取第一辆。规则确定、每次可复现。`plan` 输出会单列往返组，觉得不合适就直接改 `state/assign.json` 再 `fill`。

## 车辆数据：每次现读

`plan` 默认每次都从飞书**现读**安全员表（`Ejq5bzUqBatotAsnovScC8vYnKb` / `tbloU8rA0VU0gTjB`），不再用过期快照：

- 走 `/space/api/v1/bitable/{app}/records`，记录是 base64(gzip(JSON))。
- 安全员字段是 user 类型，取 `name` 后去掉尾部手机号（`张三-138xxxx0000` → `郁钢`）。
- 写回 `vehicles.json`，旧文件留一份 `vehicles.prev.json` 便于对比。
- cookie 失效会直接报错 → 重新复制 `~/.claude/feishu/cookie.txt`。

## 前置

| 依赖 | 说明 |
|---|---|
| `auth.json` | RT 的 Playwright storage_state，默认 `~/.claude/feishu/auth.json`；失效就在浏览器里重新登录一次再导出 |
| RT token | 复用 `~/.claude/skills/rt-base-board/config.json` 的 `rt_token`（仅 `check` 用） |
| 飞书 cookie | `~/.claude/feishu/cookie.txt`，`plan` / `vehicles` 现读安全员表时用；过期重取即可 |
| 安全员表 | 飞书 wiki `Bmt7w8ikEiTryskEjG8cKIcrneJ` 内嵌的 bitable，`app_token`/`table_id` 在 `config.json → feishu` |

## 页面结构上的坑（都已验证）

- **路线名称是 `<input>` 的 value**，不是文本节点；按 `td:nth-child(2) input.value` 匹配行。
- **车辆下拉按城市过滤**，选项文本就是 Hostname；直接用精确文本匹配选项。
- 路线表自带分页，默认 **30 条/页**，要手动切 `100 / page` 才能一页铺完。
- **「过滤同产品已配置路线」必须打开**（说明书明确要求），**每次都要开**。7835 套餐全量 **81 条**，打开后剩多少取决于任务当前状态：已配置到其他 HNP 套餐的、已经跑过的都会消失，可能是 71、61、80 等任意值；序号出现空缺属正常，不是漏排。
- 套餐全量清单固化在 `package_routes.json`（81 条，来自 RT 套餐定义）。`plan` 每次都会拿它和 `state/routes_visible.json` 对账，把被过滤的路线按模板点名列出来；如果界面里出现重名，会明确告警"过滤开关可能没开"；如果整组往返路线被过滤，会提示该组本次不排。
- **下发时间**是 antd DatePicker：先 `input.focus(); input.click()` 再派发 mousedown/mouseup/click，等面板出现后点 footer 的 `Now`，再点 `OK`。合成事件能打开，但面板有动画延迟，要轮询等待，别固定 sleep。
- 坐标点击依赖视口高度，脚本统一用 **1800×1800**。
- 所有 antd 操作都走 `dispatchEvent(new MouseEvent(...))`，比坐标点击稳，也不怕元素在可视区外。
- 中文文本用 `page.keyboard.insert_text()` 输入搜索框；`get_by_text` 对中文会超时。

## 若目标套餐不在任务上

`check` 报「不在任务上」时，用 `add_pkg.py` 追加（已验证的 DOM 点击流，不用坐标）：

```bash
python3 add_pkg.py <task_url>                  # 只选择、不保存，先验证
python3 add_pkg.py <task_url> --save           # 确认无误后保存
python3 add_pkg.py <task_url> --pkg 7835 --product HNP --save
```

流程：任务页「编辑」→ 弹窗「添加路测套餐」新增一行 → 产品下拉选 `HNP` → 名称框输入 `7835` 选中唯一命中项 → 「保存修改」→ 等 toast「路测信息保存成功」。
保存后必须用 `check` 复核：`check` 读的是任务自身 `rt_plans`（不是 `routes_beautify`，后者在未生成调度表时为空）。

注意：任务处于「已取消」时，套餐可能加了又被清掉，先确认任务状态；「追加」不会覆盖已有套餐，已有几行就再加一行。

## 输出给用户时的格式

按城市分段，先给往返组汇总（车、司机、条数、路线），再列各城市明细；最后单列"被过滤、界面不出现的路线"，并说明 `state/assign.md` 的路径。
