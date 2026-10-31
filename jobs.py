#!/usr/bin/env python3
"""jobs — 安装任务队列 + SSE 事件总线。

同一时刻只跑一个任务（防止两条通道并发抢电视的安装会话）。
"""
import os
import threading
import time
import uuid

import channels
import core


class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = []
        self.history = []
        self.seq = 0

    def publish(self, ev):
        with self.lock:
            self.seq += 1
            ev["seq"] = self.seq
            self.history.append(ev)
            if len(self.history) > 800:
                self.history = self.history[-500:]
            subs = list(self.subs)
        for q in subs:
            q.append(ev)

    def subscribe(self):
        q = []
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


HUB = Hub()
JOBS = {}
LOCK = threading.Lock()
CURRENT = {"job": None}


def log(text, level="info"):
    line = f"[{time.strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    HUB.publish({"type": "log", "level": level, "text": line})


def public(job):
    return {
        "id": job["id"], "status": job["status"], "serial": job["serial"], "opts": job["opts"],
        "items": [{"name": i["name"], "path": i["path"], "kind": i["kind"], "status": i["status"],
                   "msg": i["msg"], "meta": i["meta"]} for i in job["items"]],
    }


def _emit(job):
    HUB.publish({"type": "job", "job": public(job)})


def _run(job):
    serial, opts = job["serial"], job["opts"]
    channel = opts.get("channel", "auto")
    for it in job["items"]:
        if job["cancel"]:
            it["status"] = "canceled"
            continue
        it["status"] = "running"
        _emit(job)
        meta = it["meta"] or {}
        title = meta.get("label") or it["name"]
        log(f"===== {title}  ({meta.get('package') or it['path']}) =====")
        if meta.get("home") and not opts.get("allow_home"):
            it["status"] = "failed"
            it["msg"] = "声明了 HOME，已拦截（勾选「允许安装桌面类」才放行）"
            log(it["msg"], "warn")
            _emit(job)
            continue

        if it["kind"] == "device":
            plan = ["pm-device"]
        else:
            plan = ["adb", "tcast", "pm"] if channel == "auto" else [channel]

        ok, msg = False, ""
        for ch in plan:
            if job["cancel"]:
                break
            log(f"→ 通道 {ch}")
            try:
                if ch == "pm-device":
                    ok, msg = channels.channel_pm_device(serial, it["path"], opts, log)
                elif ch == "tcast":
                    ok, msg = channels.channel_tcast(serial, it["path"], opts, log, meta)
                else:
                    ok, msg = channels.CHANNELS[ch](serial, it["path"], opts, log)
            except Exception as e:
                ok, msg = False, f"异常：{e}"
            if ok:
                log(f"✔ {ch} 成功", "ok")
                break
            log(f"✘ {ch} 失败：{msg}", "warn" if ch != plan[-1] else "error")
            if channel != "auto":
                break

        if job["cancel"]:
            it["status"], it["msg"] = "canceled", "已取消"
        elif ok:
            it["status"] = "done"
            it["msg"] = msg
            if meta.get("package"):
                meta["installed"] = core.installed_version(serial, meta["package"])
                meta["action"] = "reinstall"
        else:
            it["status"], it["msg"] = "failed", msg
        _emit(job)

    job["status"] = "done"
    with LOCK:
        if CURRENT["job"] is job:
            CURRENT["job"] = None
    _emit(job)
    done = sum(1 for i in job["items"] if i["status"] == "done")
    log(f"===== 结束：成功 {done}/{len(job['items'])} =====",
        "ok" if done == len(job["items"]) else "warn")


def start(serial, items, opts):
    """items: [{'kind','path','name','meta'}]"""
    with LOCK:
        if CURRENT["job"]:
            raise RuntimeError("已有安装任务在跑，等它结束或先取消")
        job = {"id": uuid.uuid4().hex[:8], "serial": serial, "opts": opts,
               "items": items, "status": "running", "cancel": False}
        JOBS[job["id"]] = job
        CURRENT["job"] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def cancel():
    with LOCK:
        job = CURRENT["job"]
    if job:
        job["cancel"] = True
        log("已请求取消，当前这一步结束后停止", "warn")
        return True
    return False
