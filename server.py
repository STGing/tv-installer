#!/usr/bin/env python3
"""server — TV Installer 的 HTTP 层：静态页 + JSON API + SSE。

    python server.py                    # 按 config.json 的 lan 设置监听（默认仅本机）
    python server.py --lan              # 监听 0.0.0.0，局域网可访问（需访问码）
    python server.py --local            # 强制只监听 127.0.0.1
    python server.py --port 8780 --no-browser
"""
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import core
import jobs

UI_DIR = os.path.join(core.APP_DIR, "ui")
MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}
SAFE_PATH_RE = re.compile(r"^/[\w\-./ \u4e00-\u9fff（）()\[\]@#,+~]*$")
PIN_COOKIE = "tvpin"


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def q(text):
    return '"' + text + '"'


# --------------------------------------------------------------------------- 应用
def list_apps(serial):
    third = core.sh(serial, "pm list packages -3", timeout=60)
    pkgs = sorted(l.split("package:", 1)[1].strip() for l in third.splitlines() if l.startswith("package:"))
    dump = core.sh(serial, "dumpsys package", timeout=120)
    info = {}
    cur = None
    for line in dump.splitlines():
        m = re.match(r"\s*Package \[([\w\.]+)\]", line)
        if m:
            cur = m.group(1)
            continue
        if not cur:
            continue
        s = line.strip()
        if s.startswith("versionName="):
            info.setdefault(cur, {})["versionName"] = s.split("=", 1)[1].strip()
        elif s.startswith("versionCode="):
            info.setdefault(cur, {})["versionCode"] = s.split("=", 1)[1].split()[0]
        elif s.startswith("lastUpdateTime="):
            info.setdefault(cur, {})["lastUpdate"] = s.split("=", 1)[1].strip()
    return [{"package": p, "versionName": info.get(p, {}).get("versionName", ""),
             "versionCode": info.get(p, {}).get("versionCode", ""),
             "lastUpdate": info.get(p, {}).get("lastUpdate", ""),
             "protected": core.is_protected(p)} for p in pkgs]


def list_files(serial, path):
    if not SAFE_PATH_RE.match(path):
        raise ValueError("路径含非法字符")
    # 结尾加 / 让 /sdcard 这类软链接跟到真实目录
    out = core.sh(serial, f"ls -la {q(path.rstrip('/') + '/')}", timeout=40)
    if "No such file" in out or "Not a directory" in out:
        raise ValueError("目录不存在")
    items = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line or line.startswith("total "):
            continue
        m = re.match(r"^([dl-][rwxstST-]{9})\s+\d+\s+\S+\s+\S+\s+(\d+)\s+"
                     r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.*)$", line)
        if not m:
            continue
        perms, size, date, tm, name = m.groups()
        name = name.split(" -> ")[0]
        if name in (".", ".."):
            continue
        items.append({"name": name, "isDir": perms.startswith("d"), "size": int(size),
                      "mtime": f"{date} {tm}", "path": path.rstrip("/") + "/" + name})
    items.sort(key=lambda x: (not x["isDir"], x["name"].lower()))
    return items


def file_size(serial, path):
    out = core.sh(serial, f"toybox stat -c %s {q(path)}", timeout=30)
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TVInstaller/2.0"

    def log_message(self, *a):
        pass

    # ---- 基础
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        try:
            return json.loads(self._body().decode("utf-8") or "{}")
        except Exception:
            return {}

    def _is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _authorized(self):
        cfg = core.load_config()
        if not cfg.get("lan") or self._is_local():
            return True
        pin = parse_qs(urlparse(self.path).query).get("pin", [""])[0]
        if pin and pin == cfg.get("pin"):
            return True
        m = re.search(PIN_COOKIE + r"=([^;]+)", self.headers.get("Cookie") or "")
        return bool(m and m.group(1) == cfg.get("pin"))

    def _login_page(self):
        html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TV Installer · 访问码</title>
<style>body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f6f7f9;margin:0;
display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:26px;width:min(92vw,360px);
box-shadow:0 8px 24px rgba(16,24,40,.06)}h1{font-size:17px;margin:0 0 6px}
p{color:#6b7280;font-size:13px;margin:0 0 16px}input{width:100%;padding:12px;font-size:20px;letter-spacing:6px;
text-align:center;border:1px solid #e5e7eb;border-radius:10px;box-sizing:border-box}
button{width:100%;margin-top:12px;padding:11px;border:0;border-radius:10px;background:#2563eb;color:#fff;font-size:15px}
</style></head><body><form class="box" method="get" action="/">
<h1>TV Installer</h1><p>输入电脑端「设置」里显示的 6 位访问码</p>
<input name="pin" inputmode="numeric" maxlength="6" autofocus placeholder="······">
<button>进入</button></form></body></html>"""
        self._bytes(html.encode("utf-8"), "text/html; charset=utf-8", 401)

    # ---- 路由
    def do_GET(self):
        u = urlparse(self.path)
        p, query = u.path, parse_qs(u.query)
        if not self._authorized():
            pin = query.get("pin", [""])[0]
            if pin and pin == core.load_config().get("pin"):
                # 登录成功：种 cookie 再跳回首页，之后所有请求带 cookie
                self.send_response(302)
                self.send_header("Set-Cookie", f"{PIN_COOKIE}={pin}; Path=/; Max-Age=31536000; SameSite=Lax")
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if p == "/":
                return self._login_page()
            return self._json({"ok": False, "msg": "需要访问码"}, 401)

        if p.startswith("/api/"):
            try:
                return self._api_get(p, query)
            except Exception as e:
                return self._json({"ok": False, "msg": f"{type(e).__name__}: {e}"}, 500)

        rel = "index.html" if p == "/" else p.lstrip("/")
        full = os.path.normpath(os.path.join(UI_DIR, rel))
        if not full.startswith(UI_DIR) or not os.path.isfile(full):
            return self._bytes(b"not found", "text/plain; charset=utf-8", 404)
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            return self._bytes(f.read(), MIME.get(ext, "application/octet-stream"))

    def _api_get(self, p, query):
        serial = query.get("serial", [""])[0]
        if p == "/api/state":
            cfg = core.load_config()
            devs, err = core.list_devices()
            port = cfg["http_port"]
            return self._json({
                "adb": core.adb_path(), "aapt": (core.aapt_paths() or [None])[0],
                "devices": devs, "error": err,
                "device": core.device_info(devs[0]["serial"]) if devs else None,
                "settings": {k: cfg[k] for k in ("last_ip", "last_port", "channel", "auto_accept",
                                                 "allow_home", "grant", "downgrade", "lan", "http_port",
                                                 "protected_extra")},
                "pin": cfg.get("pin"),
                "lan_url": f"http://{local_ip()}:{port}/" if cfg.get("lan") else "",
                "uploads": core.UPLOAD_DIR,
            })
        if p == "/api/apps":
            return self._json({"apps": list_apps(serial)})
        if p == "/api/files":
            path = query.get("path", ["/sdcard"])[0] or "/sdcard"
            return self._json({"path": path, "items": list_files(serial, path)})
        if p == "/api/files/download":
            return self._download(serial, query.get("path", [""])[0])
        if p == "/api/app/export":
            return self._export(serial, query.get("package", [""])[0])
        if p == "/api/events":
            return self._sse()
        return self._json({"ok": False, "msg": "unknown api"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        p, query = u.path, parse_qs(u.query)
        if not self._authorized():
            return self._json({"ok": False, "msg": "需要访问码"}, 401)
        try:
            return self._api_post(p, query)
        except ValueError as e:
            return self._json({"ok": False, "msg": str(e)}, 400)
        except Exception as e:
            return self._json({"ok": False, "msg": f"{type(e).__name__}: {e}"}, 500)

    def _api_post(self, p, query):
        cfg = core.load_config()

        if p == "/api/connect":
            d = self._json_body()
            ip = (d.get("ip") or cfg["last_ip"]).strip()
            port = int(d.get("port") or cfg["last_port"] or 5555)
            if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                return self._json({"ok": False, "msg": "IP 格式不对"})
            r = core.adb(["connect", f"{ip}:{port}"], timeout=30)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            ok = "connected" in out.lower() and "cannot" not in out.lower() and "failed" not in out.lower()
            if ok:
                core.update_config({"last_ip": ip, "last_port": port})
            jobs.log(("已连接 " if ok else "连接失败 ") + out, "ok" if ok else "error")
            return self._json({"ok": ok, "msg": out})

        if p == "/api/disconnect":
            d = self._json_body()
            core.adb(["disconnect", d.get("serial", "")], timeout=20)
            return self._json({"ok": True})

        if p == "/api/settings":
            d = self._json_body()
            patch = {k: d[k] for k in ("last_ip", "last_port", "channel", "auto_accept", "allow_home",
                                       "grant", "downgrade", "lan", "http_port", "adb",
                                       "protected_extra") if k in d}
            if patch.get("adb") and not os.path.isfile(patch["adb"]):
                return self._json({"ok": False, "msg": "adb 路径不存在"})
            if "protected_extra" in patch:
                bad = (not isinstance(patch["protected_extra"], list)
                       or not all(isinstance(x, str) and re.match(r"^[\w\.]+$", x)
                                  for x in patch["protected_extra"]))
                if bad:
                    return self._json({"ok": False, "msg": "protected_extra 需为包名数组"})
            new = core.update_config(patch)
            return self._json({"ok": True, "settings": {k: new[k] for k in (
                "last_ip", "last_port", "channel", "auto_accept", "allow_home", "grant",
                "downgrade", "lan", "http_port", "adb", "protected_extra")}, "pin": new["pin"]})

        if p == "/api/upload":
            name = os.path.basename(unquote(query.get("name", ["app.apk"])[0])) or "app.apk"
            if not name.lower().endswith(".apk"):
                name += ".apk"
            os.makedirs(core.UPLOAD_DIR, exist_ok=True)
            dst = os.path.join(core.UPLOAD_DIR, name)
            left = int(self.headers.get("Content-Length") or 0)
            with open(dst, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            serial = query.get("serial", [""])[0]
            return self._json({"ok": True, "path": dst, "meta": core.apk_info(dst, serial or None)})

        if p == "/api/inspect":
            d = self._json_body()
            return self._json({"meta": core.apk_info(d.get("path", ""), d.get("serial"))})

        if p == "/api/install":
            d = self._json_body()
            serial = d.get("serial") or ""
            if not serial:
                devs, _ = core.list_devices()
                if not devs:
                    return self._json({"ok": False, "msg": "没有连接的电视"}, 400)
                serial = devs[0]["serial"]
            items = []
            for path in d.get("apks") or []:
                path = os.path.abspath(path)
                items.append({"kind": "local", "path": path, "name": os.path.basename(path),
                              "status": "pending", "msg": "", "meta": core.apk_info(path, serial)})
            if not items:
                return self._json({"ok": False, "msg": "没有待安装的 APK"}, 400)
            return self._start(serial, items, self._opts(d))

        if p == "/api/install-device":
            d = self._json_body()
            serial = d.get("serial") or ""
            items = []
            for path in d.get("paths") or []:
                if not SAFE_PATH_RE.match(path) or not path.lower().endswith(".apk"):
                    continue
                name = path.rstrip("/").rsplit("/", 1)[-1]
                items.append({"kind": "device", "path": path, "name": name, "status": "pending",
                              "msg": "", "meta": {"label": name, "package": None, "home": False}})
            if not items:
                return self._json({"ok": False, "msg": "没有可安装的电视端 APK"}, 400)
            return self._start(serial, items, self._opts(d))

        if p == "/api/cancel":
            return self._json({"ok": jobs.cancel()})

        if p == "/api/app":
            d = self._json_body()
            serial, pkg, act = d.get("serial"), d.get("package", ""), d.get("action")
            if not pkg or not re.match(r"^[\w\.]+$", pkg):
                return self._json({"ok": False, "msg": "包名不合法"})
            if act == "uninstall":
                if core.is_protected(pkg):
                    return self._json({"ok": False, "msg": f"{pkg} 是关键系统包，已阻止"})
                out = core.sh(serial, f"pm uninstall {pkg}", timeout=180)
                ok = "Success" in out
                jobs.log(f"卸载 {pkg} → {out or 'done'}", "ok" if ok else "error")
                return self._json({"ok": ok, "msg": out})
            if act == "launch":
                out = core.sh(serial, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1", timeout=60)
                ok = "No activities found" not in out and "aborted" not in out.lower()
                return self._json({"ok": ok, "msg": (out.splitlines() or [""])[-1]})
            if act == "stop":
                core.sh(serial, f"am force-stop {pkg}", timeout=30)
                return self._json({"ok": True, "msg": "已停止"})
            if act == "clear":
                if core.is_protected(pkg):
                    return self._json({"ok": False, "msg": "关键系统包，已阻止"})
                out = core.sh(serial, f"pm clear {pkg}", timeout=60)
                return self._json({"ok": "Success" in out, "msg": out})
            return self._json({"ok": False, "msg": "未知操作"})

        if p == "/api/app/export":
            d = self._json_body()
            return self._export(d.get("serial"), d.get("package", ""))

        if p == "/api/files/mkdir":
            d = self._json_body()
            path = d.get("path", "")
            if not core.safe_file_path(path):
                return self._json({"ok": False, "msg": "只能在 /sdcard 等用户目录下创建"})
            out = core.sh(d.get("serial"), f"mkdir -p {q(path)}", timeout=30)
            return self._json({"ok": out == "", "msg": out or "已创建"})

        if p == "/api/files/rename":
            d = self._json_body()
            old, new = d.get("path", ""), (d.get("newName") or "").strip()
            if not core.safe_file_path(old) or not new or "/" in new or not SAFE_PATH_RE.match("/" + new):
                return self._json({"ok": False, "msg": "参数不合法"})
            dst = old.rsplit("/", 1)[0] + "/" + new
            out = core.sh(d.get("serial"), f"mv {q(old)} {q(dst)}", timeout=30)
            return self._json({"ok": out == "", "msg": out or "已重命名"})

        if p == "/api/files/delete":
            d = self._json_body()
            paths = [x for x in (d.get("paths") or []) if core.safe_file_path(x)]
            if not paths:
                return self._json({"ok": False, "msg": "没有可删除的路径（系统目录已保护）"})
            out = core.sh(d.get("serial"), "rm -rf " + " ".join(q(x) for x in paths), timeout=180)
            return self._json({"ok": out == "", "msg": out or f"已删除 {len(paths)} 项"})

        if p == "/api/files/upload":
            path = unquote(query.get("path", ["/sdcard"])[0])
            name = os.path.basename(unquote(query.get("name", ["upload.bin"])[0]))
            if not name or not SAFE_PATH_RE.match(path + "/" + name):
                return self._json({"ok": False, "msg": "目标路径不合法"})
            os.makedirs(core.UPLOAD_DIR, exist_ok=True)
            tmp = os.path.join(core.UPLOAD_DIR, "_up_" + name)
            left = int(self.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            dst = path.rstrip("/") + "/" + name
            r = core.adb(["push", tmp, dst], query.get("serial", [""])[0], timeout=1800)
            try:
                os.remove(tmp)
            except OSError:
                pass
            ok = r.returncode == 0
            jobs.log(f"上传 {name} → {dst} {'成功' if ok else '失败'}", "ok" if ok else "error")
            return self._json({"ok": ok, "msg": "已上传" if ok else (r.stderr or "").strip()})

        return self._json({"ok": False, "msg": "unknown api"}, 404)

    # ---- 组装
    def _opts(self, d):
        cfg = core.load_config()
        return {
            "channel": d.get("channel") or cfg["channel"],
            "allow_home": bool(d.get("allow_home", cfg["allow_home"])),
            "downgrade": bool(d.get("downgrade", cfg["downgrade"])),
            "grant": bool(d.get("grant", cfg["grant"])),
            "auto_accept": bool(d.get("auto_accept", cfg["auto_accept"])),
        }

    def _start(self, serial, items, opts):
        try:
            job = jobs.start(serial, items, opts)
        except RuntimeError as e:
            return self._json({"ok": False, "msg": str(e)}, 409)
        return self._json({"ok": True, "job": jobs.public(job)})

    # ---- 下载 / 导出
    def _download(self, serial, path):
        if not SAFE_PATH_RE.match(path):
            return self._json({"ok": False, "msg": "路径不合法"}, 400)
        size = file_size(serial, path)
        if size is None:
            return self._json({"ok": False, "msg": "文件不存在"}, 404)
        name = path.rsplit("/", 1)[-1]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
        self.end_headers()
        try:
            proc = core.adb(["exec-out", f"cat {q(path)}"], serial, binary=True, timeout=1800)
            self.wfile.write(proc.stdout or b"")
        except Exception:
            pass

    def _export(self, serial, pkg):
        if not pkg or not re.match(r"^[\w\.]+$", pkg):
            return self._json({"ok": False, "msg": "包名不合法"}, 400)
        out = core.sh(serial, f"pm path {pkg}", timeout=30)
        m = re.search(r"package:(\S+)", out)
        if not m:
            return self._json({"ok": False, "msg": "无法定位 APK"})
        remote = m.group(1)
        try:
            proc = core.adb(["exec-out", f"cat {q(remote)}"], serial, binary=True, timeout=900)
            data = proc.stdout or b""
        except Exception as e:
            return self._json({"ok": False, "msg": str(e)}, 500)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(pkg + '.apk')}")
        self.end_headers()
        self.wfile.write(data)

    # ---- SSE
    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sub = jobs.HUB.subscribe()
        try:
            with jobs.HUB.lock:
                backlog = list(jobs.HUB.history[-150:])
            for ev in backlog:
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                if sub:
                    ev = sub.pop(0)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                else:
                    self.wfile.write(b": ping\n\n")
                    time.sleep(0.8)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            jobs.HUB.unsubscribe(sub)


def main():
    cfg = core.load_config()
    port = cfg["http_port"]
    lan = cfg["lan"]
    open_browser = True
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif a == "--local":
            lan = False
        elif a == "--lan":
            lan = True
        elif a == "--no-browser":
            open_browser = False
    os.makedirs(core.UPLOAD_DIR, exist_ok=True)

    host = "0.0.0.0" if lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("=" * 58)
    print("  TV Installer 正在运行")
    print(f"  本机： http://127.0.0.1:{port}/")
    if lan:
        print(f"  手机： http://{local_ip()}:{port}/    访问码 {cfg['pin']}")
    print(f"  adb：  {core.adb_path()}")
    print(f"  aapt： {(core.aapt_paths() or ['未找到（桌面类护栏将失效，建议装上）'])[0]}")
    print("=" * 58)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
