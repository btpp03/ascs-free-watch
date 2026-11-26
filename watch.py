#!/usr/bin/env python3
"""ASCS Cloud 免费容器：开窗自动下单 + 到货/续期通知（多账号）。

流程（每 15 分钟一跳，cron-job.org 派发）：
 1. 抓 3 个下单页的内联 `var openUntil` 判断免费档是否开窗
    （未来 epoch / 0 = 开；1 或已过期 = 关；/api/catalog 是快照，不可当判据）
 2. 对每个账号：开窗 + 该账号还没有在途免费订单 → 邮箱密码换 Bearer token，
    直接 POST /api/create-checkout 下免费单（$0，不带任何收费 addon）
 3. 已有免费订单 → 盯状态，开通/失败各推一条（MUTE_ORDERS 里的只记日志）
 4. keepalive_at 剩 < 5 天 → POST /api/micro/keepalive/<订单号> 自动续 30 天

状态存 status.json 跟仓库走，只在真变化时落盘（不产生空提交）。
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://cloud.ascsonoway.com"
PAGES = {
    "micro": (BASE + "/vps-micro/", "E5 2650 v0 免费容器（主下单页）"),
    "micro_ab": (BASE + "/alberta-micro-instances/", "i5 3470 免费容器（Alberta 页）"),
    "micro_na": (BASE + "/micro-instances-north-america/", "E5 2650 v0 免费容器（北美页）"),
}
CATALOG = BASE + "/api/catalog"
GAME_FREE_PLAN = "e5-2660v3-free"

FREE_PLAN = "E5 2650 v0 Micro Free"
FREE_PRODUCT = "Micro Instances"
FREE_OS = "Ubuntu 24.04 LTS"
DEAD_STATUS = ("cancelled", "canceled", "failed", "error", "refunded", "expired")

# 静默名单：这些订单只记日志/更新状态，不推 Telegram。
# 用户 2026-11-25 要求：现有这台不要再通知，只在新免费实例可下单时推。
# 二号 ORD-1795670706982 由 probe2.py（5 分钟一跳）单独盯，这里静默避免重复推。
MUTE_ORDERS = {"ORD-1795592770594", "ORD-1795670706982"}

# 账号列表：(显示名, 邮箱环境变量, 密码环境变量)
ACCOUNTS = [
    ("主号", "ASCS_EMAIL", "ASCS_PASSWORD"),
    ("二号", "ASCS2_EMAIL", "ASCS2_PASSWORD"),
]

STATE = "status.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "btpp03/ascs-free-watch")
REPO_URL = "https://github.com/" + REPO
UA = {"User-Agent": "Mozilla/5.0 (compatible; ascs-free-watch/2.1)"}


def log(*a):
    print(*a, flush=True)


def bj(ts=None):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime((ts or time.time()) + 8 * 3600))


def http(url, method="GET", body=None, token=None, timeout=30):
    headers = dict(UA)
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]


def parse_gate(html):
    """返回 (open, open_until)。open=None 表示页面上找不到免费档脚本。"""
    m = re.search(r"var\s+openUntil\s*=\s*(\d+)\s*;", html)
    if not m:
        return None, None
    val = int(m.group(1))
    return (val == 0 or val > time.time() * 1000), val


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


def note_once(state, key, text):
    """同一个 key 只推一次（key 里带账号和状态，避免重复刷屏）。"""
    notes = state.get("notes") or {}
    if notes.get(key):
        log("[note] %s 已推过，跳过" % key)
        return False
    if tg(text):
        notes[key] = int(time.time())
        state["notes"] = notes
        return True
    return False


def asc_login(email, pw, retries=2):
    if not email or not pw:
        log("[asc] 未配置账号邮箱/密码，跳过")
        return None
    for attempt in range(retries + 1):
        st, d = http(BASE + "/api/auth/login", "POST",
                     {"emailOrUsername": email, "password": pw})
        if st == 200 and isinstance(d, dict) and d.get("token"):
            log("[asc] 登录成功：%s" % d.get("email"))
            return d["token"]
        # 429 = 登录接口 10 次/15 分钟（per-account），退避后重试
        if st == 429 and attempt < retries:
            wait = 60 + attempt * 90
            log("[asc] 登录被限流(429)，%ds 后重试" % wait)
            time.sleep(wait)
            continue
        log("[asc] 登录失败：%s %s" % (st, str(d)[:200]))
        return None
    return None


def asc_orders(token):
    st, d = http(BASE + "/api/orders", token=token)
    if st == 200 and isinstance(d, list):
        return d
    log("[asc] 取订单失败：%s %s" % (st, str(d)[:200]))
    return None


def is_free_order(o):
    try:
        return "free" in str(o.get("plan", "")).lower() or float(o.get("amount") or 0) == 0
    except Exception:
        return False


def account_pass(tag, email, pw, opened, state, seen, renewed):
    """单个账号：下单 / 到货追踪 / 续期。"""
    token = asc_login(email, pw)
    orders = asc_orders(token) if token else None
    free_orders = [o for o in (orders or []) if is_free_order(o)]
    live = [o for o in free_orders if str(o.get("status", "")).lower() not in DEAD_STATUS]
    log("[%s] 免费订单 %d 个（在途 %d）：%s"
        % (tag, len(free_orders), len(live),
           [(o.get("id"), o.get("status")) for o in free_orders]))

    if opened and not token:
        note_once(state, "loginfail:%s:%d" % (tag, int(time.time() // 3600)),
                  "🔴 <b>ASCS 开窗了，但 %s 登录失败（可能被限流）</b>\n"
                  "请手动下单：%s\n时间：%s（北京时间）" % (tag, opened[0][1], bj()))

    if opened and token:
        if live:
            o = live[0]
            # 已有在途订单 → 只记日志，不推送（用户只要「能抢到」的通知）
            log("[%s] 开窗但已有在途订单 %s(%s)，跳过下单" % (tag, o.get("id"), o.get("status")))
        else:
            st, d = http(BASE + "/api/create-checkout", "POST", {
                "product": FREE_PRODUCT, "plan": FREE_PLAN, "os": FREE_OS,
                "billing": "month", "addons": [], "promoCode": "", "sshKey": "",
            }, token=token, timeout=60)
            log("[%s] 下单返回 %s %s" % (tag, st, str(d)[:300]))
            if st == 200 and isinstance(d, dict) and (d.get("free") or d.get("orderId")):
                tg("🟢 <b>ASCS 免费容器已自动下单（%s）</b>\n"
                   "订单：<code>%s</code>（$0，无 addon）\n"
                   "规格：%s / %s\n"
                   "状态：等待开通（站点高负载时会排队）\n"
                   "面板：https://cloud.ascsonoway.com/my-servers/\n"
                   "时间：%s（北京时间）\n仓库：%s"
                   % (tag, d.get("orderId"), FREE_PLAN, FREE_OS, bj(), REPO_URL))
                orders = asc_orders(token) or []
                free_orders = [o for o in orders if is_free_order(o)]
                live = [o for o in free_orders
                        if str(o.get("status", "")).lower() not in DEAD_STATUS]
            else:
                err = d.get("error") if isinstance(d, dict) else str(d)[:200]
                note_once(state, "orderfail:%s:%d" % (tag, int(time.time() // 3600)),
                          "🔴 <b>ASCS 免费容器下单失败（%s）</b>\n返回：%s %s\n"
                          "手动去试：https://cloud.ascsonoway.com/vps-micro/\n时间：%s（北京时间）"
                          % (tag, st, err, bj()))

    # 到货 / 失败追踪
    for o in free_orders:
        oid, status = o.get("id"), str(o.get("status", ""))
        old = seen.get(oid)
        if old != status:
            log("[%s] 订单 %s 状态 %s → %s" % (tag, oid, old, status))
            muted = oid in MUTE_ORDERS
            if status.lower() in DEAD_STATUS:
                if muted:
                    log("[%s] %s 在静默名单，跳过通知" % (tag, oid))
                else:
                    tg("🔴 <b>ASCS 免费订单失败/取消</b>\n订单：<code>%s</code> 状态：%s\n"
                       "账号：%s\n时间：%s（北京时间）" % (oid, status, tag, bj()))
            elif old is not None and status.lower() in (
                    "active", "running", "provisioned", "fulfilled", "online"):
                det = o.get("details") or {}
                if muted:
                    log("[%s] %s 在静默名单，跳过通知" % (tag, oid))
                else:
                    tg("🎉 <b>ASCS 免费容器开通了</b>\n订单：<code>%s</code> 状态：<b>%s</b>\n"
                       "账号：%s\n详情：<code>%s</code>\n"
                       "面板：https://cloud.ascsonoway.com/my-servers/\n时间：%s（北京时间）"
                       % (oid, status, tag, json.dumps(det, ensure_ascii=False)[:400], bj()))
            seen[oid] = status

    # 30 天续期（keepalive_at 剩 < 5 天就点一次）
    for o in live:
        det = o.get("details") or {}
        ka = det.get("keepalive_at")
        if not ka:
            continue
        try:
            ts = time.mktime(time.strptime(ka[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except Exception:
            continue
        left = (ts + 30 * 86400 - time.time()) / 86400.0
        log("[%s] %s keepalive_at=%s 剩余 %.1f 天" % (tag, o.get("id"), ka, left))
        if left < 5 and renewed.get(o.get("id")) != ka and token:
            st, d = http(BASE + "/api/micro/keepalive/" + urllib.parse.quote(o.get("id")),
                         "POST", {}, token=token)
            log("[%s] 续期 %s → %s %s" % (tag, o.get("id"), st, str(d)[:200]))
            if st == 200:
                renewed[o.get("id")] = ka
                if o.get("id") in MUTE_ORDERS:
                    log("[%s] %s 在静默名单，续期不通知" % (tag, o.get("id")))
                else:
                    tg("🔁 <b>ASCS 免费容器已自动续期 30 天</b>\n订单：<code>%s</code>\n"
                       "账号：%s\n时间：%s（北京时间）" % (o.get("id"), tag, bj()))


def main():
    st_old = json.load(open(STATE)) if os.path.exists(STATE) else {}
    prev_pages = st_old.get("pages") or {}
    state = dict(st_old)

    # ---------- 1. 开窗判定 ----------
    new_pages = {}
    opened = []
    for key, (url, label) in PAGES.items():
        try:
            st, html = http(url)
            if st != 200 or not isinstance(html, str):
                raise RuntimeError("HTTP %s" % st)
        except Exception as e:
            log("[%s] 抓取失败：%s（沿用上次状态）" % (key, e))
            new_pages[key] = dict(prev_pages.get(key) or {"open": None, "open_until": None})
            continue
        open_, val = parse_gate(html)
        new_pages[key] = {"open": open_, "open_until": val}
        prev = (prev_pages.get(key) or {}).get("open")
        log("[%s] open=%s openUntil=%s (prev=%s)" % (key, open_, val, prev))
        if open_ is True and prev is not True:
            # 只在「关 → 开」的跳变时动作，避免每 15 分钟重复推
            opened.append((label, url, val, prev))

    # ---------- 2. 账号侧：下单 / 到货 / 续期 ----------
    seen = state.get("order_status") or {}
    renewed = state.get("renewed") or {}
    for tag, ekey, pkey in ACCOUNTS:
        email = os.environ.get(ekey, "").strip()
        pw = os.environ.get(pkey, "").strip()
        if not email or not pw:
            log("[%s] 未配置 %s/%s，跳过" % (tag, ekey, pkey))
            continue
        try:
            account_pass(tag, email, pw, opened, state, seen, renewed)
        except Exception as e:
            log("[%s] 处理异常：%s" % (tag, e))

    # ---------- 3. 落盘 ----------
    new = {"pages": new_pages,
           "catalog": dict(state.get("catalog") or {}),
           "order_status": seen,
           "renewed": renewed,
           "notes": state.get("notes") or {}}

    # 游戏服免费档：只有目录信号（快照，仅作参考）
    try:
        st, cat = http(CATALOG)
        stock = None
        for svc in (cat.get("services") or []):
            for p in (svc.get("plans") or []):
                if p.get("id") == GAME_FREE_PLAN:
                    stock = p.get("stockStatus")
        if stock:
            prev = (state.get("catalog") or {}).get(GAME_FREE_PLAN)
            new["catalog"][GAME_FREE_PLAN] = stock
            log("[catalog] %s stock=%s (prev=%s)" % (GAME_FREE_PLAN, stock, prev))
            if stock == "in_stock" and prev != "in_stock":
                tg("🟡 <b>ASCS 游戏服免费档目录显示 in_stock</b>\n"
                   "E5 2660v3 Free（1 vCPU / 2GB / 16GB + AMP）\n"
                   "页面：https://cloud.ascsonoway.com/game-servers/\n"
                   "⚠️ 目录是快照，需到页面确认\n时间：%s（北京时间）" % bj())
    except Exception as e:
        log("[catalog] 失败：%s" % e)

    if json.dumps(new, sort_keys=True) != json.dumps(
            {k: st_old.get(k) for k in new}, sort_keys=True):
        json.dump(new, open(STATE, "w"), ensure_ascii=False, indent=1)
        log("state 有变化，已写回")
    else:
        log("state 无变化，不落盘（仓库不产生空提交）")


if __name__ == "__main__":
    main()
