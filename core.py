#!/usr/bin/env python3
"""core — 配置、adb/aapt 发现、设备信息、APK 解析。

只依赖标准库。所有 adb 调用都走 adb()，方便统一超时/编码/无窗口。
"""
import json
import os
import re
import shutil
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
IS_WIN = os.name == "nt"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

DEFAULTS = {
    "adb": None,                 # 留空自动发现
    "last_ip": "",               # 上次连接的电视 IP，首次运行留空
    "last_port": 5555,
    "channel": "auto",           # auto | adb | tcast | pm
    "auto_accept": True,         # tcast 自动点电视上的「同意」
    "allow_home": False,         # 允许安装声明 HOME 的包
    "grant": False,              # 安装时 -g 自动授权
    "downgrade": False,          # 允许降级安装
    "lan": False,                # 允许局域网（手机）访问，默认关闭：先在电脑上用
    "http_port": 8770,
    "pin": None,                 # 局域网访问 PIN，首次运行自动生成
    "protected_extra": [],       # 额外保护的应用包名（比如自己的第三方桌面）
}

# 卸载/停用会毁掉电视的包：宁可报错也不动手
PROTECTED_PKGS = {
    "android", "com.android.systemui", "com.android.settings", "com.android.providers.settings",
    "com.tcl.cyberui", "com.tcl.settings", "com.tcl.providers.config", "com.tcl.tv",
    "com.android.packageinstaller", "com.android.shell",
}

# 文件删除/改名允许的根：只碰用户数据和临时目录
SAFE_FILE_ROOTS = ("/sdcard", "/storage", "/mnt", "/data/local/tmp")


def is_protected(pkg):
    """内置保护包 + 用户自己在 config.json 的 protected_extra 里追加的包。"""
    if not pkg:
        return False
    if pkg in PROTECTED_PKGS:
        return True
    try:
        return pkg in set(load_config().get("protected_extra") or [])
    except Exception:
        return False


# --------------------------------------------------------------------------- 配置
def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except Exception:
        pass
    if not cfg.get("pin"):
        cfg["pin"] = "".join(__import__("random").choice("0123456789") for _ in range(6))
        save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("保存配置失败:", e, file=sys.stderr)


def update_config(patch):
    cfg = load_config()
    for k, v in (patch or {}).items():
        if k in DEFAULTS and v is not None:
            cfg[k] = v
    save_config(cfg)
    return cfg


# --------------------------------------------------------------------------- 工具发现
def _first_file(paths):
    for p in paths:
        if not p:
            continue
        if os.path.isfile(p):
            return os.path.abspath(p)
        w = shutil.which(p)
        if w:
            return os.path.abspath(w)
    return None


def sdk_root():
    """Android SDK 根目录：环境变量优先，不猜盘符。"""
    return (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
            or os.environ.get("ANDROID_SDK") or "")


def adb_path():
    cfg = load_config()
    sdk = sdk_root()
    cands = [cfg.get("adb"), "adb"]
    if sdk:
        cands += [os.path.join(sdk, "platform-tools", "adb.exe"),
                  os.path.join(sdk, "platform-tools", "adb")]
    if IS_WIN:
        # 官方安装器 / Android Studio 的默认位置
        cands += [
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe"),
        ]
    return _first_file(cands)


def aapt_paths():
    sdk = sdk_root()
    out = []
    bt = os.path.join(sdk, "build-tools") if sdk else ""
    if bt and os.path.isdir(bt):
        for d in sorted(os.listdir(bt), reverse=True):
            for name in ("aapt2.exe", "aapt2", "aapt.exe", "aapt"):
                p = os.path.join(bt, d, name)
                if os.path.isfile(p):
                    out.append(p)
    for name in ("aapt2", "aapt"):
        w = shutil.which(name)
        if w:
            out.append(w)
    return out


def adb(args, serial=None, timeout=120, binary=False):
    exe = adb_path() or "adb"
    cmd = [exe] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(
        cmd, capture_output=True,
        text=not binary, encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        timeout=timeout, creationflags=NO_WINDOW,
    )


def sh(serial, command, timeout=60):
    r = adb(["shell", command], serial, timeout=timeout)
    return ((r.stdout or "") + (r.stderr or "")).strip()


# --------------------------------------------------------------------------- 设备
def list_devices():
    exe = adb_path()
    if not exe:
        return [], "找不到 adb，请在「设置」里指定 adb.exe 路径"
    try:
        r = adb(["devices", "-l"], timeout=25)
    except Exception as e:
        return [], f"adb 执行失败：{e}"
    devs = []
    for line in (r.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            model = next((p[6:] for p in parts[2:] if p.startswith("model:")), "")
            devs.append({"serial": parts[0], "model": model})
    return devs, None


def device_info(serial):
    if not serial:
        return None

    def prop(name):
        try:
            return sh(serial, f"getprop {name}", timeout=15)
        except Exception:
            return ""

    try:
        size = sh(serial, "wm size", timeout=15).splitlines()
    except Exception:
        size = []
    return {
        "serial": serial,
        "model": prop("ro.product.model") or serial,
        "device": prop("ro.product.device"),
        "android": prop("ro.build.version.release"),
        "sdk": prop("ro.build.version.sdk"),
        "abi": prop("ro.product.cpu.abi"),
        "size": size[0].replace("Physical size: ", "").strip() if size else "",
    }


def tv_ip(serial):
    if serial and ":" in serial and not serial.startswith("emulator"):
        return serial.rsplit(":", 1)[0]
    try:
        out = sh(serial, "ip route", timeout=15)
    except Exception:
        return None
    m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def installed_version(serial, package):
    """返回 {'versionName','versionCode'}，未安装时 None。"""
    try:
        out = sh(serial, f"dumpsys package {package}", timeout=30)
    except Exception:
        return None
    if "Unable to find package" in out or "not found" in out:
        return None
    vn = re.search(r"versionName=(\S+)", out)
    vc = re.search(r"versionCode=(\d+)", out)
    if not vc:
        return None
    return {"versionName": vn.group(1) if vn else "", "versionCode": int(vc.group(1))}


# --------------------------------------------------------------------------- APK
def apk_info(path, serial=None):
    path = os.path.abspath(path) if path else ""
    base = os.path.basename(path) if path else ""
    info = {
        "path": path, "name": base, "size": 0, "package": None,
        "label": os.path.splitext(base)[0] if base else "", "versionName": "", "versionCode": "",
        "sdk": "", "home": False, "error": None, "installed": None, "action": "install",
    }
    if not path or not os.path.isfile(path):
        info["error"] = "文件不存在"
        return info
    info["size"] = os.path.getsize(path)

    aapts = aapt_paths()
    if not aapts:
        info["error"] = "找不到 aapt2/aapt——可直接安装，但认不出 category.HOME，桌面类护栏会失效"
        return info
    for aapt in aapts:
        try:
            r = subprocess.run([aapt, "dump", "badging", path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90,
                               creationflags=NO_WINDOW)
            out = r.stdout or ""
        except Exception:
            continue
        if "package:" not in out:
            continue
        m = re.search(r"package: name='([^']+)'", out)
        vc = re.search(r"versionCode='([^']*)'", out)
        vn = re.search(r"versionName='([^']*)'", out)
        sdk = re.search(r"sdkVersion:'([^']*)'", out)
        labels = re.findall(r"application-label(?:-[A-Za-z\-]+)?:'([^']*)'", out)
        zh = re.search(r"application-label-zh(?:-CN)?:'([^']*)'", out)
        other = (re.search(r"\bapplication: label='([^']*)'", out)
                 or re.search(r"launchable-activity: name='[^']*'\s+label='([^']*)'", out))
        label = ((zh.group(1) if zh else "") or next((x for x in labels if x), "")
                 or (other.group(1) if other else "") or info["label"])
        info.update(
            package=m.group(1) if m else None,
            versionCode=vc.group(1) if vc else "",
            versionName=vn.group(1) if vn else "",
            sdk=sdk.group(1) if sdk else "",
            label=label,
        )
        try:
            x = subprocess.run([aapt, "dump", "xmltree", "--file", "AndroidManifest.xml", path],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90, creationflags=NO_WINDOW)
            info["home"] = "android.intent.category.HOME" in (x.stdout or "")
        except Exception:
            pass
        break

    if serial and info["package"]:
        inst = installed_version(serial, info["package"])
        info["installed"] = inst
        if inst:
            try:
                new_vc = int(info["versionCode"] or 0)
            except ValueError:
                new_vc = 0
            if new_vc > inst["versionCode"]:
                info["action"] = "update"
            elif new_vc and new_vc < inst["versionCode"]:
                info["action"] = "downgrade"
            else:
                info["action"] = "reinstall"
    return info


def safe_file_path(path):
    """只允许在用户可写目录下做删除/改名，避免误伤系统分区。"""
    p = (path or "").strip()
    if not p.startswith("/"):
        return False
    if p == "/" or any(p == r for r in SAFE_FILE_ROOTS):
        return False
    return any(p.startswith(r + "/") for r in SAFE_FILE_ROOTS)
