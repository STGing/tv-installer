#!/usr/bin/env python3
"""channels — 三条安装通道。

adb  : adb install -r [-d] [-g]        最快；shell 直接 commit 会话，不产生验证广播
tcast: TCL 多屏互动 TCP 6553 推送      电视端多屏互动自己弹确认框、自己装完，
                                       不建 PackageInstaller 会话 → 同样不在校验链上（P-04）
pm   : push /data/local/tmp + pm install  兜底；也支持安装电视上已有的 APK 文件
"""
import hashlib
import http.server
import os
import re
import socket
import struct
import threading
import time

import core

TC_PORT = 6553
HTTP_PORT = 8765

ERRLIB = {
    "INSTALL_FAILED_VERIFICATION_FAILURE": "TCL 安装校验拒绝（换多屏互动通道）",
    "INSTALL_FAILED_VERIFIER_REJECTED": "安装校验器拒绝（换多屏互动通道）",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "签名与已装版本不一致，需先卸载原应用",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "目标版本更低，勾选「允许降级」再试",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "电视存储不足",
    "INSTALL_FAILED_OLDER_SDK": "APK 最低系统版本高于电视（Android 11 / SDK 30）",
    "INSTALL_FAILED_NO_MATCHING_ABIS": "APK 的 CPU 架构与电视不匹配（需 armeabi-v7a）",
    "INSTALL_PARSE_FAILED_NO_CERTIFICATES": "APK 未签名或签名损坏",
    "INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES": "新旧 APK 签名不一致，需先卸载",
    "INSTALL_FAILED_ALREADY_EXISTS": "应用已存在，加 -r 覆盖安装",
    "INSTALL_FAILED_ABORTED": "电视端取消了安装",
    "INSTALL_FAILED_USER_RESTRICTED": "电视禁止安装未知来源应用",
    "INSTALL_FAILED_INVALID_APK": "APK 结构损坏",
    "INSTALL_FAILED_DEPRECATED_SDK_VERSION": "目标 SDK 过低，系统拒绝安装",
}


def explain(text):
    for code, hint in ERRLIB.items():
        if code in text:
            return f"{code}：{hint}"
    m = re.search(r"(INSTALL_[A-Z_]+)", text)
    if m:
        return m.group(1)
    return (text.strip().splitlines() or ["未知错误"])[-1][:300]


# --------------------------------------------------------------------------- adb install
def channel_adb(serial, apk, opts, log):
    args = ["install", "-r"]
    if opts.get("downgrade"):
        args.append("-d")
    if opts.get("grant"):
        args.append("-g")
    args.append(apk)
    log(f"$ adb install {' '.join(args[1:-1])} {os.path.basename(apk)}")
    try:
        r = core.adb(args, serial, timeout=1800)
    except Exception as e:
        return False, f"adb install 失败：{e}"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in out.splitlines():
        if line.strip():
            log("  " + line.strip(), "dim")
    if "Success" in out:
        return True, "Success"
    return False, explain(out)


# --------------------------------------------------------------------------- 本地 HTTP（给电视下载）
class ApkServer:
    """任意 GET 都返回同一个 APK，支持 Range —— 电视端下载器会分段拉。"""

    def __init__(self, apk_path, port=HTTP_PORT):
        self.path = apk_path
        self.size = os.path.getsize(apk_path)
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(outer.size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

            def do_GET(self):
                start, end = 0, outer.size - 1
                rng = self.headers.get("Range")
                if rng:
                    m = re.match(r"bytes=(\d*)-(\d*)", rng)
                    if m:
                        if m.group(1):
                            start = int(m.group(1))
                        if m.group(2):
                            end = min(int(m.group(2)), outer.size - 1)
                length = max(0, end - start + 1)
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{outer.size}")
                self.end_headers()
                with open(outer.path, "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(262144, left))
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except Exception:
                            return
                        left -= len(chunk)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


# --------------------------------------------------------------------------- tcast
def _send(sock, text):
    data = text.encode()
    sock.sendall(struct.pack(">i", len(data)) + data)


def _messages(buf):
    out = []
    while len(buf) >= 4:
        n = struct.unpack(">i", buf[:4])[0]
        if n <= 0 or n > (1 << 20):
            return out, b""
        if len(buf) < 4 + n:
            break
        out.append(buf[4:4 + n].decode("utf-8", "replace"))
        buf = buf[4 + n:]
    return out, buf


def _tap_agree(serial, log):
    for _ in range(5):
        try:
            core.adb(["shell", "uiautomator", "dump", "/sdcard/tcast_ui.xml"], serial, timeout=30)
            xml = core.adb(["shell", "cat", "/sdcard/tcast_ui.xml"], serial, timeout=30).stdout or ""
        except Exception:
            return False
        m = re.search(
            r'resource-id="com\.tcl\.MultiScreenInteraction_TV:id/btn_positive"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            x = (int(m.group(1)) + int(m.group(3))) // 2
            y = (int(m.group(2)) + int(m.group(4))) // 2
            log(f"  自动点击电视上的「同意」({x},{y})")
            core.adb(["shell", f"input tap {x} {y}"], serial, timeout=20)
            return True
        time.sleep(1)
    return False


def channel_tcast(serial, apk, opts, log, meta):
    host = core.tv_ip(serial)
    if not host:
        return False, "拿不到电视 IP，多屏互动通道不可用"
    pkg = (meta or {}).get("package")
    if not pkg:
        return False, "读不到包名，多屏互动通道需要包名"
    try:
        srv = ApkServer(os.path.abspath(apk))
    except OSError as e:
        return False, f"本地 HTTP 服务启动失败（{HTTP_PORT} 被占用？）：{e}"
    sock = None
    try:
        r = core.adb(["reverse", f"tcp:{HTTP_PORT}", f"tcp:{HTTP_PORT}"], serial, timeout=20)
        if r.returncode != 0:
            return False, "adb reverse 失败：" + explain(r.stderr or "")
        url = f"http://127.0.0.1:{HTTP_PORT}/{os.path.basename(apk)}"
        size_mb = max(1, os.path.getsize(apk) // (1024 * 1024))
        log(f"  多屏互动 {host}:{TC_PORT} ← {url} ({size_mb}MB)")
        sock = socket.create_connection((host, TC_PORT), timeout=8)
        sock.settimeout(1)
        _send(sock, "159>>1>>0")
        _send(sock, ">>".join([
            "223", "INSTALL", pkg, (meta or {}).get("label") or pkg, url, "2",
            str((meta or {}).get("versionCode") or "1"), str(size_mb), "", "", "",
            hashlib.md5(url.encode()).hexdigest(),
        ]))
        buf, last_beat, deadline = b"", time.time(), time.time() + 900
        agreed = False
        while time.time() < deadline:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                pass
            except OSError as e:
                return False, f"连接中断：{e}"
            msgs, buf = _messages(buf)
            for msg in msgs:
                parts = msg.split(">>")
                if len(parts) < 5 or parts[0] != "223" or parts[2] != pkg:
                    continue
                if parts[1] == "INSTALL" and parts[4] == "confirm_pending" and not agreed:
                    if opts.get("auto_accept", True):
                        time.sleep(1.2)
                        agreed = _tap_agree(serial, log)
                        if not agreed:
                            log("  没点到「同意」，请在电视上手动确认", "warn")
                    else:
                        log("  等待你在电视上点「同意」…", "warn")
                elif parts[1] == "INSTALL" and parts[4] == "denied":
                    return False, "电视端拒绝了安装"
                elif parts[1] == "PROGRESS":
                    stage = parts[4]
                    log(f"  进度 stage={stage} {parts[5] if len(parts) > 5 else ''}%", "dim")
                    if stage == "5":
                        return True, "INSTALLED (tcast)"
                    if stage == "2":
                        return False, "电视端安装失败（多屏互动 stage=2）"
            if time.time() - last_beat > 5:
                _send(sock, "150>>")
                last_beat = time.time()
        return False, "多屏互动超时（电视休眠或未响应）"
    except socket.timeout:
        return False, f"连不上 {host}:{TC_PORT}（多屏互动服务未运行）"
    except OSError as e:
        return False, f"网络错误：{e}"
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        try:
            core.adb(["reverse", "--remove", f"tcp:{HTTP_PORT}"], serial, timeout=15)
        except Exception:
            pass
        srv.close()


# --------------------------------------------------------------------------- push + pm
def channel_pm(serial, apk, opts, log):
    remote = "/data/local/tmp/tvi_" + hashlib.md5(str(time.time()).encode()).hexdigest()[:8] + ".apk"
    try:
        log(f"$ adb push → {remote}")
        r = core.adb(["push", apk, remote], serial, timeout=1800)
        if r.returncode != 0:
            return False, "push 失败：" + explain(r.stderr or "")
        return _pm_install(serial, remote, opts, log)
    finally:
        try:
            core.adb(["shell", f"rm -f {remote}"], serial, timeout=30)
        except Exception:
            pass


def _pm_install(serial, remote, opts, log):
    args = ["shell", "pm", "install", "-r"]
    if opts.get("downgrade"):
        args.append("-d")
    if opts.get("grant"):
        args.append("-g")
    args.append(remote)
    log("$ adb shell pm install " + " ".join(args[3:-1]) + f" {os.path.basename(remote)}")
    r = core.adb(args, serial, timeout=900)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in out.splitlines():
        if line.strip():
            log("  " + line.strip(), "dim")
    if "Success" in out:
        return True, "Success (pm)"
    return False, explain(out)


def channel_pm_device(serial, device_path, opts, log):
    """直接安装电视上已有的 APK 文件，不经过电脑。"""
    if not device_path.lower().endswith(".apk"):
        return False, "不是 .apk 文件"
    return _pm_install(serial, device_path, opts, log)


CHANNELS = {"adb": channel_adb, "tcast": channel_tcast, "pm": channel_pm}
