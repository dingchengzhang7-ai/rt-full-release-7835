"""从飞书文档内嵌的多维表格读取「HNP-Imdevcar车辆&安全员」。

文档 wiki/Bmt7w8ikEiTryskEjG8cKIcrneJ 里嵌的是 bitable（app_token 见 config.json），
走 /space/api/v1/bitable/{app}/records，返回 data.records 是 base64(gzip(JSON))。
"""
import base64
import gzip
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TEXT, SELECT, USER = 1, 3, 11
COLS = ["车牌", "Hostname", "安全员", "车型", "当前使用", "城市", "调度状态", "测试内容", "备注"]


def _cookie(path):
    p = Path(path).expanduser()
    if not p.exists():
        sys.exit(f"缺少飞书 cookie: {p}（浏览器打开飞书 → F12 → 任意请求复制 cookie 串）")
    return p.read_text(encoding="utf-8").strip()


def _ungzip(value):
    if isinstance(value, str) and value.startswith("H4sI"):
        return json.loads(gzip.decompress(base64.b64decode(value)).decode("utf-8"))
    return value


def _get(host, cookie, path, params):
    url = f"{host}/space/api/v1/bitable/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/156.0.0.0 Safari/537.36",
        "accept-encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"飞书接口报错: {payload.get('code')} {payload.get('msg')}")
    return payload["data"]


def _clean_person(raw):
    """安全员字段是 {'users': [{'name': '张三-138xxxx0000', ...}]}，取姓名去掉手机号。"""
    if not raw:
        return ""
    if isinstance(raw, dict):
        users = raw.get("users") or []
        name = (users[0].get("name") if users else "") or ""
    else:
        m = re.search(r"'name':\s*'([^']+)'", str(raw))
        name = m.group(1) if m else str(raw)
    return re.sub(r"-\d{6,}$", "", name).strip()


def _render(field, cell):
    if not cell:
        return ""
    val = cell.get("value")
    if val is None:
        return ""
    ftype = field.get("type")
    if ftype == SELECT:
        names = {o["id"]: o["name"] for o in field.get("property", {}).get("options", [])}
        picked = val if isinstance(val, list) else [val]
        return ", ".join(names.get(v, str(v)) for v in picked)
    if ftype == USER:
        return _clean_person(val)
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("name") or "")
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(val)


def fetch(feishu_cfg, limit=500):
    host = feishu_cfg["host"]
    cookie = _cookie(feishu_cfg["cookie_file"])
    app, table, view = feishu_cfg["app_token"], feishu_cfg["table_id"], feishu_cfg.get("view_id", "")

    meta = _get(host, cookie, f"{app}/clientvars", {
        "tableID": table, "viewID": view, "recordLimit": limit, "needBase": "true",
        "openType": 0, "ondemandVer": 2, "viewLazyLoad": "true",
        "optimizationFlag": 1, "removeFmlExtra": "true",
    })
    fields = _ungzip(meta["table"])["fieldMap"]

    data = _get(host, cookie, f"{app}/records", {
        "tableId": table, "viewId": view, "viewLazyLoad": "true", "offset": 0,
        "limit": limit, "tableID": table, "viewID": view, "removeFmlExtra": "true",
    })
    record_map = _ungzip(data["records"])["recordMap"]

    by_name = {f["name"]: fid for fid, f in fields.items()}
    rows = []
    for rec in record_map.values():
        row = {name: _render(fields[fid], rec.get(fid)) for name, fid in by_name.items() if name in COLS}
        if row.get("车牌"):
            rows.append({c: row.get(c, "") for c in COLS})

    order = {"上海（创新港）": 0, "上海（金海园）": 1}
    rows.sort(key=lambda r: (order.get(r["城市"], 9), r["城市"], r["调度状态"] != "可调度", r["车牌"]))
    return rows
