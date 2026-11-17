"""Run Python inside the running Unreal Editor via PythonScriptPlugin Remote Execution.

Requires Project Settings > Plugins > Python > Enable Remote Execution (bind address 127.0.0.1).
Uses the engine's own remote_execution.py client.

Usage:
  .venv/bin/python unreal/ue_remote.py -c "print(unreal.SystemLibrary.get_engine_version())"
  .venv/bin/python unreal/ue_remote.py unreal/some_script.py [json-args]
A script file runs with `ARGS` (parsed JSON, default {}) defined in its globals.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ENGINE_PY = Path("/Users/Shared/Epic Games/UE_5.8/Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python")
sys.path.insert(0, str(ENGINE_PY))
import remote_execution as rex  # noqa: E402

# macOS workaround: the editor binds its discovery socket to 127.0.0.1:6766, and on BSD stacks a socket bound
# to a unicast address never receives datagrams addressed to the multicast group. So (a) also send every
# discovery message unicast to 127.0.0.1:<port>, and (b) bind our own socket to all interfaces so the editor's
# multicast replies (looped back on 127.0.0.1) still reach us.
_Broadcast = rex._RemoteExecutionBroadcastConnection
_orig_init_socket = _Broadcast._init_broadcast_socket


def _init_socket_any(self):
    bind_address = self._config.multicast_bind_address
    self._config.multicast_bind_address = ""
    try:
        sock = rex._socket.socket(rex._socket.AF_INET, rex._socket.SOCK_DGRAM, rex._socket.IPPROTO_UDP)
        sock.setsockopt(rex._socket.SOL_SOCKET, rex._socket.SO_REUSEPORT, 1)
        sock.bind(("", self._config.multicast_group_endpoint[1]))
        iface = rex._socket.inet_aton(bind_address)
        sock.setsockopt(rex._socket.IPPROTO_IP, rex._socket.IP_MULTICAST_LOOP, 1)
        sock.setsockopt(rex._socket.IPPROTO_IP, rex._socket.IP_MULTICAST_TTL, self._config.multicast_ttl)
        sock.setsockopt(rex._socket.IPPROTO_IP, rex._socket.IP_MULTICAST_IF, iface)
        sock.setsockopt(rex._socket.IPPROTO_IP, rex._socket.IP_ADD_MEMBERSHIP,
                        rex._socket.inet_aton(self._config.multicast_group_endpoint[0]) + iface)
        sock.settimeout(0.1)
        self._broadcast_socket = sock
    finally:
        self._config.multicast_bind_address = bind_address


def _broadcast_both(self, message):
    data = message.to_json_bytes()
    self._broadcast_socket.sendto(data, self._config.multicast_group_endpoint)
    self._broadcast_socket.sendto(data, (self._config.multicast_bind_address, self._config.multicast_group_endpoint[1]))


if sys.platform == "darwin":
    _Broadcast._init_broadcast_socket = _init_socket_any
    _Broadcast._broadcast_message = _broadcast_both


def run(code, exec_mode=rex.MODE_EXEC_FILE, timeout=20.0):
    cfg = rex.RemoteExecutionConfig()
    cfg.multicast_bind_address = "127.0.0.1"
    remote = rex.RemoteExecution(cfg)
    remote.start()
    try:
        deadline = time.time() + timeout
        while not remote.remote_nodes and time.time() < deadline:
            time.sleep(0.2)
        if not remote.remote_nodes:
            raise SystemExit("No Unreal Editor found. Is Remote Execution enabled and the editor running?")
        remote.open_command_connection(remote.remote_nodes[0]["node_id"])
        result = remote.run_command(code, unattended=True, exec_mode=exec_mode)
        for line in result.get("output", []):
            print(line.get("output", "").rstrip())
        if not result.get("success"):
            print(result.get("result"), file=sys.stderr)
            raise SystemExit(1)
        return result
    finally:
        remote.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script", nargs="?")
    ap.add_argument("args", nargs="?", default="{}")
    ap.add_argument("-c", dest="code")
    a = ap.parse_args()
    if a.code:
        run("import unreal\n" + a.code)
    else:
        src = Path(a.script).read_text()
        run(f"import unreal, json\nARGS = json.loads({a.args!r})\n" + src)


if __name__ == "__main__":
    main()
