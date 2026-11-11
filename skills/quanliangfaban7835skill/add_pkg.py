"""在 RT 任务里追加一个发版套餐（默认只选择、不保存；加 --save 才写线上）。

用法：
  python add_pkg.py <task_url> [--pkg 7835] [--product HNP] [--save]
"""
import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HOME = Path.home() / ".claude" / "feishu"
OUT = Path(__file__).resolve().parent / "state"

MODAL_JS = """
  const ms = [...document.querySelectorAll('.ant-modal')].filter(m => {
    const r = m.getBoundingClientRect();
    return r.width > 300 && getComputedStyle(m).display !== 'none'
      && (m.innerText||'').includes('编辑路测信息');
  });
  const m = ms[ms.length - 1];
"""

CLICK_BTN = """(label) => { %s
  if (!m) return 'no modal';
  const b = [...m.querySelectorAll('button')].find(e =>
    (e.innerText||'').replace(/\\s/g,'') === label);
  if (!b) return 'no btn';
  b.scrollIntoView({block:'center'});
  ['mouseover','mousedown','mouseup','click'].forEach(t =>
    b.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
  return 'clicked';
}""" % MODAL_JS

OPEN_SEL = """(txt) => { %s
  if (!m) return 'no modal';
  const s = [...m.querySelectorAll('.ant-select')].find(e =>
    (e.innerText||'').replace(/\\u200b/g,'').trim() === txt);
  if (!s) return 'nf';
  s.scrollIntoView({block:'center'});
  const t = s.querySelector('.ant-select-selector') || s;
  ['mouseover','mousedown','mouseup','click'].forEach(x =>
    t.dispatchEvent(new MouseEvent(x, {bubbles:true, cancelable:true, view:window})));
  return 'opened';
}""" % MODAL_JS

PICK_OPT = """(txt) => [...document.querySelectorAll(
    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
  .find(o => (o.innerText||'').replace(/\\u200b/g,'').trim() === txt)
  ?.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}))
  || 'nf';"""

OPTS = """() => [...document.querySelectorAll(
    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
  .map(o => (o.innerText||'').replace(/\\u200b/g,'').trim()).filter(Boolean).slice(0, 80)"""

FOCUS_NAME = """() => { %s
  if (!m) return 'no modal';
  const s = [...m.querySelectorAll('.ant-select')].find(e =>
    (e.innerText||'').replace(/\\u200b/g,'').trim() === '名称（支持搜索）');
  if (!s) return 'nf';
  const i = s.querySelector('input');
  if (!i) return 'no input';
  i.focus();
  i.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
  return 'focused';
}""" % MODAL_JS

STATE = """() => { %s
  if (!m) return {found: false};
  const rows = [...m.querySelectorAll('.ant-select')].map(s =>
    (s.innerText||'').replace(/\\u200b/g,'').trim()).filter(Boolean);
  return {found: true, rows};
}""" % MODAL_JS

TOAST = """() => [...document.querySelectorAll('.ant-message-notice, .ant-notification-notice')]
  .map(e => (e.innerText||'').trim().slice(0,200))"""

ERRS = """() => [...document.querySelectorAll('.ant-modal .ant-form-item-explain-error')]
  .map(e => (e.innerText||'').trim()).filter(Boolean)"""


def dump(x):
    print(json.dumps(x, ensure_ascii=False))


async def select(page, label, value, timeout_ms=4000):
    print(f"  选择 {label} = {value} …", await page.evaluate(OPEN_SEL, label))
    deadline = timeout_ms
    while deadline > 0:
        await page.wait_for_timeout(400)
        opts = await page.evaluate(OPTS)
        if opts:
            break
        deadline -= 400
    print("    选项:", opts[:15])
    if value not in opts:
        return f"选项里没有 {value}"
    await page.evaluate(PICK_OPT, value)
    await page.wait_for_timeout(800)
    return "ok"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--pkg", default="7835")
    ap.add_argument("--product", default="HNP")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        ctx = await browser.new_context(storage_state=str(HOME / "auth.json"),
                                        viewport={"width": 1800, "height": 1400})
        page = await ctx.new_page()
        writes = []
        page.on("response", lambda r: writes.append(
            (r.request.method, r.status, r.url.split("?")[0]))
            if "rt.momenta" in r.url and r.request.method in ("POST", "PUT") else None)

        await page.goto(args.task, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(12000)

        await page.evaluate("""() => {
          const b = [...document.querySelectorAll('button')].find(e =>
            (e.innerText||'').replace(/\\s/g,'') === '编辑');
          if (b) b.scrollIntoView({block:'center'});
        }""")
        await page.wait_for_timeout(600)
        await page.evaluate("""() => {
          const b = [...document.querySelectorAll('button')].find(e =>
            (e.innerText||'').replace(/\\s/g,'') === '编辑');
          if (!b) return 'no edit';
          ['mouseover','mousedown','mouseup','click'].forEach(t =>
            b.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
          return 'clicked';
        }""")
        await page.wait_for_timeout(3000)
        print("打开编辑弹窗:", (await page.evaluate(STATE)).get("found"))

        print("添加路测套餐:", await page.evaluate(CLICK_BTN, "添加路测套餐"))
        await page.wait_for_timeout(1500)

        r1 = await select(page, "产品", args.product)
        print("  产品 →", r1)
        if r1 != "ok":
            await page.screenshot(path=str(OUT / "add_fail.png"), full_page=True)
            await browser.close()
            sys.exit("产品选择失败")

        print("  聚焦名称:", await page.evaluate(FOCUS_NAME))
        await page.wait_for_timeout(300)
        await page.keyboard.insert_text(args.pkg)
        opts = []
        for _ in range(8):
            await page.wait_for_timeout(500)
            opts = await page.evaluate(OPTS)
            if opts:
                break
        print("    名称选项:", opts)
        hit = next((o for o in opts if args.pkg in o), None)
        if not hit:
            await page.screenshot(path=str(OUT / "add_fail.png"), full_page=True)
            await browser.close()
            sys.exit(f"名称里没有含 {args.pkg} 的套餐")
        await page.evaluate(PICK_OPT, hit)
        await page.wait_for_timeout(1200)

        st = await page.evaluate(STATE)
        print("当前弹窗选择:", json.dumps(st, ensure_ascii=False))
        await page.screenshot(path=str(OUT / "add_row.png"), full_page=True)

        if not args.save:
            print("\n未保存（dry run）。确认无误后加 --save 重跑。")
            await browser.close()
            return

        print("保存修改:", await page.evaluate(CLICK_BTN, "保存修改"))
        for i in range(10):
            await page.wait_for_timeout(1200)
            t = await page.evaluate(TOAST)
            e = await page.evaluate(ERRS)
            if t or e:
                print(f"[{i}] toast={t} errs={e}")
                break
        await page.screenshot(path=str(OUT / "add_saved.png"), full_page=True)
        print("写请求:", json.dumps(writes, ensure_ascii=False, indent=1))
        await browser.close()
        print("已保存。用 check 复核。")


asyncio.run(main())
