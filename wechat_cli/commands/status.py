"""status 命令 — 判断微信账号登录状态"""

import time
from datetime import datetime

import click

from ..core.process import find_listen_ports, find_wechat_pids, process_names
from ..output.formatter import output

_STATE_HINT = {
    "online": "微信已登录",
    "offline": "微信已掉线，请在微信中重新扫码登录",
    "not_running": "微信未运行",
    "unknown": "无法判定登录状态（端口探测不可用）",
}

# 登录瞬间 LISTEN 端口可能尚未拉起，重试后再判定掉线
_PROBE_ATTEMPTS = 3
_PROBE_INTERVAL = 0.5


def _probe():
    """采集一次信号，返回 (pids, listen_ports, unknown)。"""
    pids = find_wechat_pids()
    if not pids:
        return [], set(), False
    ports = set()
    unknown = False
    for pid in pids:
        result = find_listen_ports(pid)
        if result is None:
            unknown = True
        else:
            ports |= result
    return pids, ports, unknown


def _detect():
    """判定登录状态，返回 (state, pids, listen_ports)。"""
    pids, ports, unknown = _probe()

    # 仅在「进程存在但未探测到 LISTEN」时重试，规避登录竞态
    for _ in range(_PROBE_ATTEMPTS - 1):
        if not pids or ports or unknown:
            break
        time.sleep(_PROBE_INTERVAL)
        pids, ports, unknown = _probe()

    if not pids:
        return "not_running", [], set()
    if ports:
        return "online", pids, ports
    if unknown:
        return "unknown", pids, ports
    return "offline", pids, ports


@click.command("status")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="输出格式")
def status(fmt):
    """判断微信账号的登录状态

    \b
    示例:
      wechat-cli status                # JSON 输出登录状态
      wechat-cli status --format text  # 纯文本输出

    \b
    判定依据: 微信主进程在回环地址上的 TCP LISTEN 端口。
    登录后拉起内部 IPC 服务（有 LISTEN），退出登录后全部关闭（无 LISTEN）。
    """
    state, pids, ports = _detect()
    result = {
        "state": state,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "process": {
            "running": bool(pids),
            "pids": pids,
            "names": process_names(),
        },
        "signals": {
            "local_listen_ports": sorted(ports),
        },
        "hint": _STATE_HINT[state],
    }

    if fmt == "json":
        output(result, "json")
    else:
        lines = [
            f"微信状态: {state}",
            f"进程: {'运行中' if pids else '未运行'}  PIDs={pids or '-'}",
            f"本地 LISTEN 端口: {sorted(ports) or '无'}",
            f"提示: {_STATE_HINT[state]}",
        ]
        output("\n".join(lines), "text")