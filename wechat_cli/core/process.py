"""微信进程与登录态探测 — 跨平台。

登录态判据（macOS 已实测验证）：
    微信主进程在登录后会在回环地址 127.0.0.1 上拉起一组 TCP LISTEN 端口
    （内部 IPC 服务），退出登录后全部关闭。因此：
        进程存在 + 回环 LISTEN 端口 > 0  -> 已登录
        进程存在 + 回环 LISTEN 端口 = 0  -> 已掉线
    远端连接（:443 / :80）在两种状态下都存在且会抖动，无判别力，故不参与判定。

注意：LISTEN 判据目前仅在 macOS（WeChat 4.x）实测通过，Windows/Linux
的等价行为尚未验证，探测不可用时统一返回 None，由调用方按「未知」处理。
"""

import os
import platform
import re
import shutil
import subprocess

_SYSTEM = platform.system().lower()

# 各平台微信主进程名
_PROCESS_NAMES = {
    "darwin": ["WeChat"],
    "linux": ["wechat", "weixin"],
    "windows": ["Weixin.exe"],
}

# 匹配回环地址上的端口，如 127.0.0.1:14013 / [::1]:14013 / localhost:14013
_LOOPBACK_RE = re.compile(r"(?:127\.0\.0\.1|\[::1\]|localhost):(\d+)")

_TIMEOUT = 5


def process_names():
    """返回当前平台的微信进程名候选列表。"""
    return list(_PROCESS_NAMES.get(_SYSTEM, []))


def _run(cmd):
    """执行命令，失败（命令缺失 / 超时）返回 None。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None


def find_wechat_pids():
    """返回所有微信主进程 PID（升序）。探测不可用或无进程时返回空列表。"""
    if _SYSTEM == "darwin":
        return _pids_macos()
    if _SYSTEM == "linux":
        return _pids_linux()
    if _SYSTEM == "windows":
        return _pids_windows()
    return []


def find_listen_ports(pid):
    """返回 pid 在回环地址上的 TCP LISTEN 端口集合。

    返回 None 表示探测不可用（命令缺失 / 执行失败），调用方应视为「未知」。
    """
    if _SYSTEM == "darwin":
        return _listen_macos(pid)
    if _SYSTEM == "linux":
        return _listen_linux(pid)
    if _SYSTEM == "windows":
        return _listen_windows(pid)
    return None


# ---- macOS ----

def _pids_macos():
    pids = set()
    for name in _PROCESS_NAMES["darwin"]:
        r = _run(["pgrep", "-x", name])
        if r is None:
            continue
        pids.update(int(x) for x in r.stdout.split() if x.isdigit())
    return sorted(pids)


def _listen_macos(pid):
    if not shutil.which("lsof"):
        return None
    r = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)])
    if r is None:
        return None
    return _extract_ports(r.stdout)


# ---- Linux ----

def _pids_linux():
    names = {n.lower() for n in _PROCESS_NAMES["linux"]}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    pids = []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", encoding="utf-8") as f:
                comm = f.read().strip().lower()
        except OSError:
            continue
        if comm in names:
            pids.append(int(entry))
    return sorted(pids)


def _listen_linux(pid):
    if shutil.which("ss"):
        r = _run(["ss", "-tlnp"])
        if r is None:
            return None
        return _extract_ports(r.stdout, pid_marker=pid)
    if shutil.which("lsof"):
        return _listen_macos(pid)
    return None


# ---- Windows ----

def _pids_windows():
    pids = set()
    for name in _PROCESS_NAMES["windows"]:
        r = _run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"])
        if r is None:
            continue
        for line in r.stdout.splitlines():
            parts = [p.strip('"') for p in line.strip().split('","')]
            if len(parts) >= 2 and parts[0].lower() == name.lower() and parts[1].isdigit():
                pids.add(int(parts[1]))
    return sorted(pids)


def _listen_windows(pid):
    if not shutil.which("netstat"):
        return None
    r = _run(["netstat", "-ano", "-p", "TCP"])
    if r is None:
        return None
    ports = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING" or parts[-1] != str(pid):
            continue
        m = _LOOPBACK_RE.search(parts[1])
        if m:
            ports.add(int(m.group(1)))
    return ports


# ---- 解析 ----

def _extract_ports(text, pid_marker=None):
    """从 lsof / ss 输出中提取回环 LISTEN 端口。

    pid_marker 不为 None 时（ss 输出含所有进程），仅保留该 pid 所在行。
    """
    ports = set()
    for line in text.splitlines():
        if pid_marker is not None and not re.search(rf"\bpid={pid_marker}\b", line):
            continue
        m = _LOOPBACK_RE.search(line)
        if m:
            ports.add(int(m.group(1)))
    return ports