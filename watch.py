#!/usr/bin/env python3
"""ASCS Cloud (cloud.ascsonoway.com) 免费容器「开窗」盯梢。

只做一件事：免费档从「关」变成「开」时推一条 Telegram，其余时间静默。
状态存 status.json 跟仓库走，避免重复推送。

判据（2026-11-25 实测）：
  页面免费卡片里的内联脚本有一个运营方开关
      var openUntil = <int>;   // 1 = 关闭
  逻辑 `if (openUntil && Date.now() >= openUntil) return;`
  ⇒ openUntil 为 0 / null / 未来 epoch = 开；1 或已过期 epoch = 关。
  ⚠️ /api/catalog 是快照，关窗后仍可能显示 in_stock（11-24 就踩过），不能当主判据。
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

PAGES = {
    "micro": ("https://cloud.ascsonoway.com/vps-micro/", "E5 2650 v0 免费容器（主下单页）"),
    "micro_ab": ("https://cloud.ascsonoway.com/alberta-micro-instances/", "i5 3470 免费容器（Alberta 页）"),
    "micro_na": ("https://cloud.ascsonoway.com/micro-instances-north-america/", "E5 2650 v0 免费容器（北美页）"),
}
CATALOG = "https://cloud.ascsonoway.com/api/catalog"
GAME_FREE_PLAN = "e5-2660v3-free"
STATE = "status.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "btpp03/ascs-free-watch")
REPO_URL = "https://github.com/" + REPO
UA = {"User-Agent": "Mozilla/5.0 (compatible; ascs-free-watch/1.0)"}


def log(*a):
    print(*a, flush=True)


def bj(ts=None):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime((ts or time.time()) + 8 * 3600))


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_gate(html):
    """返回 (open, open_until)。open=None 表示页面上找不到免费档脚本。"""
    m = re.search(r"var\s+openUntil\s*=\s*(\d+)\s*;", html)
    if not m:
        return None, None
    val = int(m.group(1))
    now_ms = time.time() * 1000
    return (val == 0 or val > now_ms), val


def tg(text):
    tok = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not chat:
        log("[tg] 未配置 TG_BOT_TOKEN/TG_CHAT_ID，跳过通知")
        return False
    data = {"chat_id": chat, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % tok,
        data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            res = json.loads(r.read().decode())
        log("[tg] ok=", res.get("ok"), res.get("description", ""))
        return bool(res.get("ok"))
    except Exception as e:
        log("[tg] 失败:", e)
        return False


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def main():
    st = load_state()
    prev_pages = st.get("pages") or {}
    prev_catalog = st.get("catalog") or {}
    new_pages = {}
    alerts = []

    for key, (url, label) in PAGES.items():
        try:
            html = get(url)
        except Exception as e:
            log("[%s] 抓取失败：%s（沿用上次状态）" % (key, e))
            new_pages[key] = dict(prev_pages.get(key) or {"open": None, "open_until": None})
            continue
        open_, val = parse_gate(html)
        new_pages[key] = {"open": open_, "open_until": val}
        prev = (prev_pages.get(key) or {}).get("open")
        log("[%s] open=%s openUntil=%s (prev=%s)" % (key, open_, val, prev))
        if open_ is True and prev is not True:
            alerts.append((label, url, val))

    # 游戏服免费档：页面没有 openUntil 开关，只能看目录库存（标注需人工确认）
    catalog_flip = False
    try:
        cat = json.loads(get(CATALOG))
        stock = None
        for svc in cat.get("services", []):
            for p in svc.get("plans", []):
                if p.get("id") == GAME_FREE_PLAN:
                    stock = p.get("stockStatus")
        log("[catalog] %s stock=%s (prev=%s) updatedAt=%s"
            % (GAME_FREE_PLAN, stock, prev_catalog.get(GAME_FREE_PLAN), cat.get("updatedAt")))
        if stock == "in_stock" and prev_catalog.get(GAME_FREE_PLAN) != "in_stock":
            catalog_flip = True
        if stock:
            prev_catalog[GAME_FREE_PLAN] = stock
    except Exception as e:
        log("[catalog] 抓取失败：%s" % e)

    for label, url, val in alerts:
        until = ""
        if val and val > 1:
            until = "\n窗口截止：%s（北京时间，页面 openUntil）" % bj(val / 1000)
        tg(
            "🟢 <b>ASCS 免费容器开窗了</b>\n"
            "档位：%s\n"
            "下单页：%s%s\n"
            "规格：1 vCPU / 512MB / 16GB SSD，公网 IPv6，30 天手动续期\n"
            "注册：邮箱即可，无卡无手机；Free 卡片点 Buy Now 直接下单\n"
            "⚠️ 窗口很短（11-24 放 80 台，隔天就关），看到就马上下单\n"
            "时间：%s（北京时间）\n仓库：%s"
            % (label, url, until, bj(), REPO_URL)
        )

    if catalog_flip:
        tg(
            "🟡 <b>ASCS 游戏服免费档目录显示 in_stock</b>\n"
            "E5 2660v3 Free（1 vCPU / 2GB / 16GB SSD，带 AMP 面板）\n"
            "页面：https://cloud.ascsonoway.com/game-servers/\n"
            "⚠️ 目录是快照，11-24 就出现过「目录 in_stock、页面实际已关」，"
            "请到页面确认 Free 卡片能否点。\n"
            "时间：%s（北京时间）" % bj()
        )

    old = {"pages": prev_pages, "catalog": st.get("catalog") or {}}
    new = {"pages": new_pages, "catalog": prev_catalog}
    if alerts or catalog_flip:
        new["last_notify"] = int(time.time())
    # 只有状态真的变了才落盘 → 仓库不会每跳一个提交
    if json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True) or \
            st.get("last_notify") != new.get("last_notify"):
        st["pages"] = new_pages
        st["catalog"] = prev_catalog
        if alerts or catalog_flip:
            st["last_notify"] = int(time.time())
        json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=1)
        log("state 有变化，已写回")
    else:
        log("state 无变化，不落盘（仓库不产生空提交）")


if __name__ == "__main__":
    main()
