# ascs-free-watch

盯 [ASCS Cloud（cloud.ascsonoway.com）](https://cloud.ascsonoway.com/vps-micro/) 的免费容器档：**开窗才推 Telegram，其余时间静默**。

## 为什么不是"查库存"

这家的免费档不是一直卖，而是运营方手动开窗：

```js
var openUntil = 1; // CLOSED 2026-11-25 by operator
// restocked 2026-11-24: 80 units, E5 2650 v0 rig (.13)
```

`openUntil` 是未来 epoch（或 0）＝开窗；`1` 或已过期 ＝ 关闭，而且服务器端同样拒绝免费订单。
**`/api/catalog` 不能当判据**：它只是快照，11-24 关窗后仍显示 `in_stock`，会误报（已经踩过）。

## 判据

| 来源 | 判据 |
|---|---|
| `/vps-micro/` | 内联 `var openUntil` |
| `/alberta-micro-instances/` | 内联 `var openUntil` |
| `/micro-instances-north-america/` | 内联 `var openUntil` |
| `/game-servers/` 免费档 | 只能看目录 `e5-2660v3-free` 的 `stockStatus`，标注"需页面确认" |

状态 `false → true` 才推；状态存 `status.json` 跟着仓库提交，避免重复推。

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
TG_BOT_TOKEN=... TG_CHAT_ID=... python3 watch.py
```
