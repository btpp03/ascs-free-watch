#!/usr/bin/env python3
"""Create cron-job.org leg: dispatch btpp03/ascs-free-watch every 5 minutes."""
import json
import re
import time
import urllib.error
import urllib.request

KEY = re.search(r'KEY = "([^"]+)"', open("/root/cj_farm.py").read()).group(1)
PAT = open("/root/.gh_tok").read().strip()
BASE = "https://api.cron-job.org"
MINUTES = list(range(0, 60, 5))

body = {
    "job": {
        "title": "ascs-free-watch-dispatch",
        "url": "https://api.github.com/repos/btpp03/ascs-free-watch/actions/workflows/watch.yml/dispatches",
        "enabled": True,
        "saveResponses": True,
        "requestMethod": 1,
        "requestTimeout": 30,
        "redirectSuccess": False,
        "schedule": {
            "timezone": "UTC",
            "expiresAt": 0,
            "hours": [-1],
            "mdays": [-1],
            "minutes": MINUTES,
            "months": [-1],
            "wdays": [-1],
        },
        "extendedData": {
            "headers": {
                "Authorization": "token " + PAT,
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "User-Agent": "cron-job.org",
            },
            "body": json.dumps({"ref": "main"}),
        },
        "notification": {"onFailure": True, "onFailureCount": 5, "onDisable": True},
    }
}


def api(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + KEY,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


if __name__ == "__main__":
    st, resp = api("PUT", "/jobs", body)
    print("create:", st, resp)
    jid = resp.get("jobId")
    if not jid:
        raise SystemExit(1)
    time.sleep(3)
    st, d = api("GET", "/jobs/%s" % jid)
    j = d.get("jobDetails", {})
    hdrs = ((j.get("extendedData") or {}).get("headers") or {})
    print("readback: id=%s title=%s enabled=%s method=%s minutes=%s auth=%s next=%s"
          % (jid, j.get("title"), j.get("enabled"), j.get("requestMethod"),
             (j.get("schedule") or {}).get("minutes"),
             "Authorization" in hdrs, j.get("nextExecution")))
