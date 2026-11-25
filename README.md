# ascs-free-watch

盯 [ASCS Cloud（cloud.ascsonoway.com）](https://cloud.ascsonoway.com/vps-micro/) 的免费容器档：**开窗就自动下单，然后推 Telegram；其余时间静默**。

## 为什么不是"查库存"

这家的免费档不是一直卖，而是运营方手动开窗：

```js
var openUntil = 1; // CLOSED 2026-11-25 by operator
// restocked 2026-11-24: 80 units, E5 2650 v0 rig (.13)
```

`openUntil` 是未来 epoch（或 0）＝开窗；`1` 或已过期 ＝ 关闭。
**`/api/catalog` 不能当判据**：它只是快照，11-24 关窗后仍显示 `in_stock`，会误报（已经踩过）。

## 判据

| 来源 | 判据 |
|---|---|
| `/vps-micro/` | 内联 `var openUntil` |
| `/alberta-micro-instances/` | 内联 `var openUntil` |
| `/micro-instances-north-america/` | 内联 `var openUntil` |
| `/game-servers/` 免费档 | 只能看目录 `e5-2660v3-free` 的 `stockStatus`，标注"需页面确认" |

## 自动下单（2026-11-25 实测可行）

页面关窗时只是隐藏了按钮，**服务端并不拒单**：

```bash
POST /api/auth/login      {"emailOrUsername": "...", "password": "..."}   → token
POST /api/create-checkout {"product":"Micro Instances","plan":"E5 2650 v0 Micro Free",
                           "os":"Ubuntu 24.04 LTS","billing":"month",
                           "addons":[],"promoCode":"","sshKey":""}       → {"free":true,"orderId":"ORD-..."}
```

实测关窗状态下照样返回 `{"free":true,"orderId":"ORD-1795592770594"}`，订单状态 `pending`（等开通）。
⇒ 开窗一跳就能自己抢，不用人盯。

**去重**：账号里只要有非 cancelled/failed 的免费订单就不再下单，避免重复。

## 其他自动化

- 到货：订单状态 `pending → active/running` 推一条（带 details）
- 续期：`details.keepalive_at` 剩 < 5 天时 `POST /api/micro/keepalive/<订单号>` 自动续 30 天
- 状态存 `status.json` 跟仓库提交，只在真变化时落盘（不产生空提交）

## 没有 API key

平台只有邮箱密码换 Bearer token（`ascs_auth_token`），没有 key 管理页/开发者页；
所有面板操作都是同一套 REST（`/api/orders`、`/api/server/<订单号>/status`、`/api/micro/keepalive/<订单号>`）。

## 定时

- GitHub 原生 `schedule: */5`：账号实测常丢 tick，只当备份。
- 主腿：cron-job.org 每 5 分钟 dispatch 本 workflow。

## 免费档实际规格

- E5 2650 v0：1 vCPU / 512MB / 16GB SSD（目录写 256MB，页面写 512MB，以页面为准）
- L5630：2 vCPU / 2GB / 10GB SSD（目录有这条，但页面上没有卡片）
- 公网 IPv6，无 IPv4（IPv4 走 NAT 加 $4/月）
- 邮箱注册，无卡无手机；30 天到面板点一次续期

## 手动跑

```bash
ASCS_EMAIL=... ASCS_PASSWORD=... TG_BOT_TOKEN=... TG_CHAT_ID=... python3 watch.py
```
