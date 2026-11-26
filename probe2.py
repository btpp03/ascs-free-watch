#!/usr/bin/env python3
"""ASCS 二号账号专用高频探针：只看 ORD-1795670706982，一 active 立刻推。

设计：
 - 每 5 分钟一跳（cron-job.org 派发），单账号只登录 1 次，远低于 10 次/15 分钟限流
 - 不碰开窗判定、不下单，避免和主 watch.py 抢单
 - 状态写 state2.json，只在变化时落盘 + 推送
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://cloud.ascsonoway.com"
STATE = "state2.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; ascs-probe2/1.0)"}
LIVE_STATUS = ("active", "running", "provisioned", "fulfilled", "online")
DEAD_STATUS = ("cancelled", "canceled", "failed", "error", "refunded", "expired")


def log(*a):
    print(*a, flush=True)


def bj():
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600))


def http(url, method="GET", body=None, token=None, timeout=40):
    h = dict(UA)
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=h, method=method)
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


def tg(text):
    tok = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not chat:
        log("[tg] 未配置，跳过")
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


def main():
    email = os.environ.get("ASCS2_EMAIL", "").strip()
    pw = os.environ.get("ASCS2_PASSWORD", "").strip()
    watch_id = os.environ.get("ASCS2_ORDER", "ORD-1795670706982").strip()
    if not email or not pw:
        log("[p2] 缺 ASCS2_EMAIL/ASCS2_PASSWORD")
        return

    st, d = http(BASE + "/api/auth/login", "POST",
                 {"emailOrUsername": email, "password": pw})
    if st != 200 or not isinstance(d, dict) or not d.get("token"):
        log("[p2] 登录失败 %s %s" % (st, str(d)[:150]))
        return
    token = d["token"]

    st, orders = http(BASE + "/api/orders", token=token)
    if st != 200 or not isinstance(orders, list):
        log("[p2] 取订单失败 %s %s" % (st, str(orders)[:150]))
        return

    target = None
    for o in orders:
        if o.get("id") == watch_id:
            target = o
            break
    if not target:
        log("[p2] 没找到订单 %s" % watch_id)
        return

    status = str(target.get("status", ""))
    det = target.get("details") or {}
    log("[p2] %s status=%s ipv6=%s" % (watch_id, status, det.get("ipv6")))

    old = {}
    if os.path.exists(STATE):
        try:
            old = json.load(open(STATE))
        except Exception:
            old = {}
    prev = old.get(watch_id)

    if prev != status:
        log("[p2] 状态 %s → %s" % (prev, status))
        if status.lower() in LIVE_STATUS:
            tg("✅ <b>二号容器已 active</b>\n"
               "订单：<code>%s</code>\n"
               "账号：%s\n"
               "IPv6：<code>%s</code>\n"
               "用户/密码：<code>%s</code> / <code>%s</code>\n"
               "面板：https://cloud.ascsonoway.com/my-servers/\n"
               "时间：%s（北京时间）"
               % (watch_id, email, det.get("ipv6") or "（未分配）",
                  det.get("micro_user") or "micro", det.get("micro_pass") or "（见面板）", bj()))
        elif status.lower() in DEAD_STATUS:
            tg("🔴 <b>二号订单失败/取消</b>\n订单：<code>%s</code> 状态：%s\n时间：%s（北京时间）"
               % (watch_id, status, bj()))
        else:
            log("[p2] 非终态，不推送")
        old[watch_id] = status
        json.dump(old, open(STATE, "w"), ensure_ascii=False, indent=1)
        log("[p2] state2.json 已更新")
    else:
        log("[p2] 状态未变，不落盘不推送")


if __name__ == "__main__":
    main()
