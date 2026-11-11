"""RT 调度排车：dump 可排路线 -> plan 出方案 -> fill 回填车辆/下发时间。

永不点击弹窗里的「调度」按钮。
"""
import argparse
import asyncio
import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

SKILL = Path(__file__).resolve().parent
CFG = json.loads((SKILL / "config.json").read_text(encoding="utf-8"))
STATE = SKILL / "state"
STATE.mkdir(exist_ok=True)


def expand(p):
    return Path(str(p)).expanduser()


# ---------------------------------------------------------------- 页面脚本

OPEN_MODAL = """(kw) => {
  const row = [...document.querySelectorAll('tbody tr')].find(tr => (tr.innerText||'').includes(kw));
  const b = row && [...row.querySelectorAll('button')].find(e => (e.innerText||'').trim() === '开始调度');
  if (!b) return 'no 开始调度 button';
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    b.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'clicked';
}"""

FIND_BY_LABEL = """(label) => {
  const nodes = [...document.querySelectorAll('label, span, div, td, th')]
    .filter(e => (e.textContent||'').replace(/\\u200b/g,'').trim() === label);
  if (!nodes.length) return null;
  let p = nodes[0];
  for (let i = 0; i < 6 && p; i++) {
    const sel = p.querySelector && p.querySelector('.ant-select');
    if (sel) { const s = sel.querySelector('.ant-select-selector') || sel; const r = s.getBoundingClientRect();
      return {kind: 'select', x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)}; }
    const sw = p.querySelector && p.querySelector('.ant-switch');
    if (sw) { const r = sw.getBoundingClientRect();
      return {kind: 'switch', checked: sw.className.includes('ant-switch-checked'),
              x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)}; }
    p = p.parentElement;
  }
  return null;
}"""

MODAL_JS = """const modal = [...document.querySelectorAll('.ant-modal')].filter(m =>
    (m.innerText||'').includes('调度套餐') && getComputedStyle(m).display !== 'none').pop();
  const tbl = modal && [...modal.querySelectorAll('table')].find(t =>
    (t.innerText||'').includes('路线名称'));
"""

PICK_OPT = """(target) => {
  const dds = [...document.querySelectorAll('.ant-select-dropdown')].filter(d =>
    getComputedStyle(d).display !== 'none' && d.getBoundingClientRect().width > 0);
  const dd = dds[dds.length - 1];
  if (!dd) return 'no dropdown';
  const items = [...dd.querySelectorAll('.ant-select-item-option')];
  const it = items.find(e => (e.innerText||'').replace(/\\u200b/g,'').trim() === target);
  if (!it) return 'nf:' + items.map(e => e.innerText.trim()).join('|');
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    it.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'ok';
}"""

OPEN_PAGER = """() => { %s
  const sel = modal && modal.querySelector('.ant-pagination-options .ant-select');
  const s = sel && (sel.querySelector('.ant-select-selector') || sel);
  if (!s) return 'no pager';
  ['mousedown','mouseup','click'].forEach(t =>
    s.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'opened';
}""" % MODAL_JS

ROWS = """() => { %s
  if (!tbl) return [];
  return [...tbl.querySelectorAll('tbody tr')].map(tr => {
    const td = tr.querySelectorAll('td');
    const cell = i => {
      const t = td[i];
      if (!t) return null;
      const inp = t.querySelector('input');
      return inp ? inp.value : (t.innerText||'').replace(/\\u200b/g,'').trim();
    };
    return {seq: cell(0), name: cell(1), city: cell(2), laps: cell(3), veh: cell(4), when: cell(5)};
  });
}""" % MODAL_JS

ROW = """(name) => { %s
  if (!tbl) return {err: 'no table'};
  const norm = e => ((e && e.innerText) || '').replace(/\\u200b/g, '').trim();
  const tr = [...tbl.querySelectorAll('tbody tr')].find(t => {
    const i = t.querySelectorAll('td')[1].querySelector('input'); return i && i.value === name; });
  if (!tr) return {err: 'row nf'};
  const td = tr.querySelectorAll('td');
  const inp = td[5].querySelector('input');
  return {veh: norm(td[4]), when: inp ? inp.value : null};
}""" % MODAL_JS

OPEN_VEH = """(name) => { %s
  if (!tbl) return 'no table';
  const tr = [...tbl.querySelectorAll('tbody tr')].find(t => {
    const i = t.querySelectorAll('td')[1].querySelector('input'); return i && i.value === name; });
  if (!tr) return 'row nf';
  const sel = tr.querySelectorAll('td')[4].querySelector('.ant-select-selector');
  if (!sel) return 'no select';
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    sel.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'opened';
}""" % MODAL_JS

OPEN_TIME = """(name) => { %s
  if (!tbl) return 'no table';
  const tr = [...tbl.querySelectorAll('tbody tr')].find(t => {
    const i = t.querySelectorAll('td')[1].querySelector('input'); return i && i.value === name; });
  if (!tr) return 'row nf';
  const inp = tr.querySelectorAll('td')[5].querySelector('input');
  if (!inp) return 'no input';
  inp.focus(); inp.click();
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    inp.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'opened';
}""" % MODAL_JS

PICKER_OPEN = """() => {
  const dds = [...document.querySelectorAll('.ant-picker-dropdown')].filter(d =>
    getComputedStyle(d).display !== 'none' && d.getBoundingClientRect().width > 0);
  return !!dds.length;
}"""

CLICK_NOW = """() => {
  const dds = [...document.querySelectorAll('.ant-picker-dropdown')].filter(d =>
    getComputedStyle(d).display !== 'none' && d.getBoundingClientRect().width > 0);
  const dd = dds[dds.length - 1];
  if (!dd) return 'no picker';
  const el = [...dd.querySelectorAll('a, button')].find(e => (e.innerText||'').trim() === 'Now');
  if (!el) return 'no Now';
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'now';
}"""

CLICK_OK = """() => {
  const dds = [...document.querySelectorAll('.ant-picker-dropdown')].filter(d =>
    getComputedStyle(d).display !== 'none' && d.getBoundingClientRect().width > 0);
  const dd = dds[dds.length - 1];
  if (!dd) return 'no picker';
  const el = [...dd.querySelectorAll('button')].find(e => (e.innerText||'').trim() === 'OK');
  if (!el) return 'no OK';
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'ok';
}"""

CLICK_DISPATCH = """() => {
  const m = [...document.querySelectorAll('.ant-modal')].filter(x =>
    (x.innerText||'').includes('调度套餐') && getComputedStyle(x).display !== 'none').pop();
  if (!m) return 'no modal';
  const b = [...m.querySelectorAll('button')]
    .find(e => (e.innerText||'').replace(/\\s/g, '') === '调度');
  if (!b) return 'no 调度 button';
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    b.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
  return 'clicked';
}"""

CONFIRM_YES = """() => {
  const modals = [...document.querySelectorAll('.ant-modal-confirm, .ant-modal')].filter(m =>
    getComputedStyle(m).display !== 'none' && m.getBoundingClientRect().width > 0);
  for (const m of modals.reverse()) {
    const b = [...m.querySelectorAll('button')]
      .find(e => ['确定', '确认', 'OK'].includes((e.innerText||'').replace(/\\s/g, '')));
    if (b) {
      ['mouseover','mousedown','mouseup','click'].forEach(t =>
        b.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window})));
      return 'confirmed:' + (m.innerText||'').replace(/\\s+/g,' ').slice(0, 60);
    }
  }
  return 'no confirm';
}"""

READ_TOAST = """() => {
  const t = [...document.querySelectorAll('.ant-message-notice, .ant-notification-notice')]
    .map(e => (e.innerText||'').replace(/\\s+/g,' ').trim()).filter(Boolean);
  return t.join(' | ');
}"""


# ---------------------------------------------------------------- 公共

def rt_token():
    for p in (CFG["rt_token_config"], "~/.rt-env/rt_token.json"):
        cfg = expand(p)
        if cfg.exists():
            return json.loads(cfg.read_text(encoding="utf-8"))["rt_token"]
    raise SystemExit(f"找不到 RT token：{CFG['rt_token_config']} 或 ~/.rt-env/rt_token.json")


def api_get(path):
    req = urllib.request.Request(
        "https://rt.momenta.works" + path,
        headers={"Authorization": rt_token(), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def task_id(url):
    return url.rstrip("/").split("/")[-1]


async def open_schedule(page, task, pkg, pool):
    await page.goto(task, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(12000)
    r = await page.evaluate(OPEN_MODAL, pkg)
    print("打开调度弹窗:", r)
    await page.wait_for_timeout(6000)

    node = await page.evaluate(FIND_BY_LABEL, "资源池")
    if not node:
        raise SystemExit("找不到「资源池」下拉，弹窗可能没打开")
    await page.mouse.click(node["x"], node["y"])
    await page.wait_for_timeout(1500)
    await page.keyboard.insert_text("组内组间")
    await page.wait_for_timeout(2500)
    print("选资源池:", await page.evaluate(PICK_OPT, pool))
    await page.wait_for_timeout(6000)

    flt = await page.evaluate(FIND_BY_LABEL, "过滤同产品已配置路线")
    if flt and flt["kind"] == "switch" and not flt["checked"]:
        await page.mouse.click(flt["x"], flt["y"])
        await page.wait_for_timeout(5000)
    flt = await page.evaluate(FIND_BY_LABEL, "过滤同产品已配置路线")
    print("过滤同产品已配置路线:", "开" if flt and flt["checked"] else "关!!")


async def new_page(p):
    from playwright.async_api import async_playwright  # noqa: F401
    browser = await p.chromium.launch(channel="chrome", headless=True)
    ctx = await browser.new_context(storage_state=str(expand(CFG["auth_state"])),
                                    viewport={"width": 1800, "height": 1800})
    return browser, await ctx.new_page()


# ---------------------------------------------------------------- check

def cmd_check(args):
    tid = task_id(args.task)
    task = api_get(f"/backend/tbd/console/api/dlp/rt-manager/{tid}")["data"]
    print(f"任务：{task.get('name')}")
    print(f"状态：{task.get('status')}")
    plans = [p.get("road_test_plan_new") or {} for p in (task.get("rt_plans") or [])]
    print(f"套餐：{len(plans)} 个")
    for x in plans:
        print(f"  - {x.get('name')} | {x.get('product')} | "
              f"{len(x.get('route_template_ids') or [])} 个模板")
    hit = [x for x in plans if args.pkg in (x.get("name") or "")]
    print("目标套餐:", "在任务上" if hit else "不在任务上（需要先添加）")


# ---------------------------------------------------------------- dump

def cmd_dump(args):
    asyncio.run(_dump(args))


async def _dump(args):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser, page = await new_page(p)
        try:
            await open_schedule(page, args.task, args.pkg, CFG["pool"])
            print("分页:", await page.evaluate(OPEN_PAGER))
            await page.wait_for_timeout(1500)
            print("每页条数:", await page.evaluate(PICK_OPT, CFG["page_size"]))
            await page.wait_for_timeout(5000)

            rows = [r for r in await page.evaluate(ROWS) if r.get("name")]
            out = STATE / "routes_visible.json"
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\n可排路线 {len(rows)} 条 -> {out}")
            for r in rows:
                print(f"  {r['seq']:>3} {r['name']} | {r['city']}")
        finally:
            await browser.close()


# ---------------------------------------------------------------- vehicles

def refresh_vehicles():
    import feishu_vehicles
    rows = feishu_vehicles.fetch(CFG["feishu"])
    out = SKILL / CFG["vehicles_file"]
    if out.exists():
        out.with_suffix(".prev.json").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def cmd_vehicles(args):
    rows = refresh_vehicles()
    ok = [r for r in rows if r["调度状态"] == "可调度"]
    print(f"安全员表：{len(rows)} 台，可调度 {len(ok)} 台 -> {SKILL / CFG['vehicles_file']}")
    by_city = {}
    for r in ok:
        by_city[r["城市"]] = by_city.get(r["城市"], 0) + 1
    for city, n in sorted(by_city.items()):
        print(f"  {city:<12} {n} 台")


# ---------------------------------------------------------------- plan

def _dedupe_report(routes):
    """套餐全量 vs 界面可见。平台会过滤掉「已在其他套餐配置」和「已经跑过」的路线，
    所以每次剩多少条都不一样，这里只做对账展示，不假设固定条数。"""
    full_file = SKILL / "package_routes.json"
    if not full_file.exists():
        print("（缺 package_routes.json，跳过去重对账）")
        return []

    full = json.loads(full_file.read_text(encoding="utf-8"))
    seen, dups = set(), []
    for r in routes:
        if r["name"] in seen:
            dups.append(r["name"])
        seen.add(r["name"])
    dropped = [x for x in full if x["route"] not in seen]

    print(f"去重对账：套餐全量 {len(full)} 条 → 本次可排 {len(routes)} 条，"
          f"平台过滤 {len(dropped)} 条（已配置到其他套餐或已经跑过，属正常）")
    if dups:
        print(f"  ⚠ 界面里出现重名 {len(dups)} 条（过滤开关可能没开）：{' / '.join(dups)}")
    if not routes:
        print("  ⚠ 界面一条路线都没有：检查过滤开关、资源池、分页是否正常")
    rt_names = {r for rs in CFG["round_trip_groups"].values() for r in rs}
    hit = [x["route"] for x in dropped if x["route"] in rt_names]
    if hit:
        print(f"  ⚠ 其中 {len(hit)} 条属于往返组，本次不会排：{' / '.join(hit)}")
    for gname, groutes in CFG["round_trip_groups"].items():
        if all(r not in seen for r in groutes):
            print(f"  ⚠ 往返组「{gname}」整组被过滤，本次不排（车也不用留）")
    by_tpl = {}
    for x in dropped:
        by_tpl.setdefault(x["template"], []).append(x["route"])
    for tpl, names in by_tpl.items():
        print(f"  {tpl}：{len(names)} 条")
        for n in names:
            print(f"    - {n}")
    print()
    return dropped


def cmd_plan(args):
    routes = json.loads((STATE / "routes_visible.json").read_text(encoding="utf-8"))
    dropped = _dedupe_report(routes)
    if args.no_refresh:
        vehicles = json.loads((SKILL / CFG["vehicles_file"]).read_text(encoding="utf-8"))
        print("车辆清单：用本地缓存（--no-refresh）")
    else:
        vehicles = refresh_vehicles()
        print(f"车辆清单：刚从安全员表现读，{len(vehicles)} 台")
    rt_group = {r: g for g, rs in CFG["round_trip_groups"].items() for r in rs}
    cross_groups = set(CFG.get("cross_city_groups") or [])

    def is_cross(name):
        return rt_group.get(name) in cross_groups

    seg_of = CFG["city_to_segment"]
    chuangxin = set(CFG["shanghai_chuangxin_routes"])

    def cars(city):
        return [v for v in vehicles
                if v.get("调度状态") == "可调度" and v.get("城市") == city]

    segments, order = {}, []
    for r in routes:
        city = r["city"]
        if city == "上海":
            seg = "上海（创新港）" if r["name"] in chuangxin else "上海（金海园）"
        else:
            seg = seg_of.get(city)
            if not seg:
                raise SystemExit(f"城市 {city} 没有映射到资源池段，请补 config.json city_to_segment")
        segments.setdefault(seg, []).append(r["name"])
        order.append((seg, r))

    assign = {}
    for seg, names in segments.items():
        vs = cars(seg)
        if not vs:
            raise SystemExit(f"{seg} 没有可调度车辆")
        # 往返组优先给自营车队（LC6/HC2/QZH），再按车牌升序，保证可复现
        vs = sorted(vs, key=lambda v: (
            0 if re.match(r"^(LC6|HC2|QZH)-", v["Hostname"]) else 1, v["车牌"]))
        rts = [n for n in names if n in rt_group]
        rest = [n for n in names if n not in rt_group]
        if rts:
            for n in rts:
                # 跨城往返组同一辆车，但不设下发时间（先不排）
                assign[n] = (vs[0], not is_cross(n))
            pool = vs[1:] or vs[:1]
        else:
            pool = vs
        for i, n in enumerate(rest):
            assign[n] = (pool[i % len(pool)], False)

    # 先排不跨城的，跨城的排后面（稳定排序）
    order.sort(key=lambda x: is_cross(x[1]["name"]))

    rows = []
    for seg, r in order:
        car, is_rt = assign[r["name"]]
        rows.append({
            "seq": r["seq"], "route": r["name"], "segment": seg,
            "group": rt_group.get(r["name"], ""),
            "plate": car["车牌"], "host": car["Hostname"], "driver": car["安全员"],
            "dispatch": "now" if is_rt else "",
        })

    (STATE / "assign.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with io.open(STATE / "assign.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    lines = ["| # | 城市 | 路线 | 车辆 | Hostname | 安全员 | 下发时间 | 往返组 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for x in rows:
        lines.append(f"| {x['seq']} | {x['segment']} | {x['route']} | {x['plate']} | "
                     f"{x['host']} | {x['driver']} | "
                     f"{'立即' if x['dispatch'] else ''} | {x['group']} |")
    (STATE / "assign.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"路线 {len(rows)} 条 | 往返组 {sum(1 for x in rows if x['dispatch'])} 条\n")

    rts = [x for x in rows if x["dispatch"] == "now"]
    if rts:
        print("### 往返组（同一辆车 + 下发时间 now）")
        groups = {}
        for x in rts:
            groups.setdefault((x["group"], x["plate"], x["host"], x["driver"]), []).append(x["route"])
        for (g, plate, host, driver), names in groups.items():
            print(f"   {g}")
            print(f"     {plate} {host} {driver} — {len(names)} 条")
            print(f"     {' / '.join(names)}")
        print()

    cross_pending = [x for x in rows if is_cross(x["route"])]
    if cross_pending:
        print("### 跨城路线（已排车，暂不下发）")
        groups = {}
        for x in cross_pending:
            groups.setdefault((x["group"], x["plate"], x["host"], x["driver"]), []).append(x["route"])
        for (g, plate, host, driver), names in groups.items():
            print(f"   {g}")
            print(f"     {plate} {host} {driver} — {len(names)} 条")
            print(f"     {' / '.join(names)}")
        print()

    for seg in segments:
        sub = [x for x in rows if x["segment"] == seg]
        load = {}
        for x in sub:
            load.setdefault((x["plate"], x["host"], x["driver"]), 0)
            load[(x["plate"], x["host"], x["driver"])] += 1
        print(f"### {seg}  {len(sub)} 条")
        for (plate, host, driver), n in load.items():
            print(f"   {plate:<10} {host:<14} {driver:<6} {n} 条")
        print()
    print(f"明细：{STATE / 'assign.md'} / assign.csv / assign.json")
    print("\n下一步需要用户确认。确认后二选一：")
    print("  只回填、不下发：python3 rt_sched.py fill --yes")
    print("  回填并最终调度：python3 rt_sched.py fill --yes --dispatch --confirm-dispatch")


# ---------------------------------------------------------------- fill

def cmd_fill(args):
    if not args.yes:
        print("拒绝执行：fill 会真实改写线上调度表单。确认方案无误后加 --yes 重跑。")
        return
    if args.dispatch and not args.confirm_dispatch:
        print("--dispatch 还需要 --confirm-dispatch，避免误触发最终下发。")
        return
    asyncio.run(_fill(args))


async def _set_now(page, name):
    for _ in range(3):
        if await page.evaluate(OPEN_TIME, name) != "opened":
            await page.wait_for_timeout(400)
            continue
        opened = False
        for _ in range(6):
            await page.wait_for_timeout(400)
            if await page.evaluate(PICKER_OPEN):
                opened = True
                break
        if not opened:
            continue
        if await page.evaluate(CLICK_NOW) != "now":
            continue
        await page.wait_for_timeout(400)
        await page.evaluate(CLICK_OK)
        await page.wait_for_timeout(400)
        if ((await page.evaluate(ROW, name)).get("when") or "").strip():
            return True
    return False


async def _fill(args):
    from playwright.async_api import async_playwright
    plan = json.loads((STATE / "assign.json").read_text(encoding="utf-8"))
    if args.limit:
        plan = plan[:args.limit]

    async with async_playwright() as p:
        browser, page = await new_page(p)
        try:
            await open_schedule(page, args.task, args.pkg, CFG["pool"])
            await page.evaluate(OPEN_PAGER)
            await page.wait_for_timeout(1500)
            print("每页条数:", await page.evaluate(PICK_OPT, CFG["page_size"]))
            await page.wait_for_timeout(5000)

            bad_v, bad_t = [], []
            for i, item in enumerate(plan, 1):
                name, host = item["route"], item["host"]
                ok = False
                for _ in range(3):
                    if await page.evaluate(OPEN_VEH, name) != "opened":
                        await page.wait_for_timeout(400)
                        continue
                    await page.wait_for_timeout(700)
                    r = await page.evaluate(PICK_OPT, host)
                    if r != "ok":
                        print(f"[{i}] {name} 选车失败 {r}")
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(400)
                        continue
                    await page.wait_for_timeout(500)
                    if host in ((await page.evaluate(ROW, name)).get("veh") or ""):
                        ok = True
                        break
                if not ok:
                    bad_v.append(name)

                if item["dispatch"] == "now" and not await _set_now(page, name):
                    bad_t.append(name)
                if i % 10 == 0:
                    print(f"  ... {i}/{len(plan)}")

            final = [await page.evaluate(ROW, x["route"]) for x in plan]
            veh_ok = sum(1 for x, s in zip(plan, final) if x["host"] in (s.get("veh") or ""))
            rts = sum(1 for x in plan if x["dispatch"] == "now")
            time_ok = sum(1 for x, s in zip(plan, final)
                          if x["dispatch"] == "now" and (s.get("when") or "").strip())
            print(f"\n车辆 {veh_ok}/{len(plan)} | 下发时间 {time_ok}/{rts}")
            if bad_v:
                print("车辆失败:", bad_v)
            if bad_t:
                print("下发时间失败:", bad_t)
            (STATE / "fill_result.json").write_text(json.dumps(
                [{"route": x["route"], "want": x["host"], "got": s}
                 for x, s in zip(plan, final)], ensure_ascii=False, indent=1), encoding="utf-8")
            await page.screenshot(path=str(STATE / "filled.png"))

            if not args.dispatch:
                print("\n已停止在「调度」之前，页面未提交。")
                return

            complete = veh_ok == len(plan) and time_ok == rts and not bad_v and not bad_t
            if not complete:
                print(f"\n回填不完整（车辆 {veh_ok}/{len(plan)}，下发时间 {time_ok}/{rts}），"
                      "拒绝点调度。")
                return

            print("\n点击「调度」…")
            r = await page.evaluate(CLICK_DISPATCH)
            print("  调度按钮:", r)
            if r != "clicked":
                return
            await page.wait_for_timeout(2500)
            print("  二次确认:", await page.evaluate(CONFIRM_YES))
            await page.wait_for_timeout(6000)
            toast = await page.evaluate(READ_TOAST)
            print("  提示:", toast or "(未捕获)")
            await page.screenshot(path=str(STATE / "dispatched.png"))
            (STATE / "dispatch_result.json").write_text(json.dumps(
                {"clicked": True, "toast": toast, "rows": len(plan),
                 "veh_ok": veh_ok, "time_ok": time_ok},
                ensure_ascii=False, indent=1), encoding="utf-8")
            print("调度已提交。请到任务页确认状态（未配置 → 待执行/执行中）。")
        finally:
            await browser.close()


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="RT 调度排车（永不点调度）")
    ap.add_argument("--task", default=CFG["task_url"])
    ap.add_argument("--pkg", default=CFG["pkg_keyword"])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="只读：任务状态与套餐列表")
    sub.add_parser("dump", help="打开调度弹窗，导出可排路线")
    sub.add_parser("vehicles", help="从飞书安全员表现读车辆清单")

    p = sub.add_parser("plan", help="按路线+车辆生成分配方案并打印")
    p.add_argument("--no-refresh", action="store_true",
                   help="复用本地 vehicles.json，不去飞书现读")

    f = sub.add_parser("fill", help="回填车辆与下发时间（需要 --yes）")
    f.add_argument("--yes", action="store_true")
    f.add_argument("--limit", type=int, default=0, help="只处理前 N 行，用于试跑")
    f.add_argument("--dispatch", action="store_true",
                   help="填完后点击最终「调度」（需配合 --confirm-dispatch）")
    f.add_argument("--confirm-dispatch", action="store_true",
                   help="用户已明确同意执行最终调度")

    args = ap.parse_args()
    {"check": cmd_check, "dump": cmd_dump, "vehicles": cmd_vehicles,
     "plan": cmd_plan, "fill": cmd_fill}[args.cmd](args)


if __name__ == "__main__":
    main()
