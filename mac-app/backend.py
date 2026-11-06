#!/usr/bin/env python3
"""Loopback API for the Electron control panel. No target app is injected."""

import argparse
import json
import os
import re
import socket
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.parse import unquote
from urllib.request import urlopen

import frida

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from obs_stream_server import connect_obs, obs_request


ROOT = Path(__file__).resolve().parent.parent
BRIDGE = None
LOCK = threading.RLock()
ICON_CACHE = {}
PACKAGE = "dev.iosobs.vcam"
SETTINGS_PATH = Path.home() / "Library/Application Support/Frame Link/settings.json"
LEGACY_SETTINGS_PATH = Path.home() / "Library/Application Support/OBS iPhone Studio/settings.json"


def load_obs_config():
    try:
        path = SETTINGS_PATH if SETTINGS_PATH.exists() else LEGACY_SETTINGS_PATH
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    return {
        "mode": data.get("mode", "websocket"),
        "host": data.get("host", "127.0.0.1"),
        "port": data.get("port", 4455),
        "password": data.get("password", ""),
        "rtmpUrl": data.get("rtmpUrl", ""),
    }


OBS_CONFIG = load_obs_config()


def phone():
    return frida.get_usb_device(timeout=5)


def mac_ip():
    for iface in ("en0", "en1"):
        result = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return "indisponível"


def bridge_pid():
    result = subprocess.run(["lsof", "-tiTCP:8080", "-sTCP:LISTEN"], capture_output=True, text=True)
    for candidate in result.stdout.splitlines():
        if not candidate.isdigit():
            continue
        pid = int(candidate)
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
        if "obs_stream_server.py" in cmd:
            return pid
    return None


def frame_ok():
    try:
        with urlopen("http://127.0.0.1:8080/health", timeout=1) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def obs_connection():
    password = OBS_CONFIG["password"]
    if not password and OBS_CONFIG["host"] in ("127.0.0.1", "localhost"):
        config = Path.home() / "Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
        try:
            password = json.loads(config.read_text()).get("server_password", "")
        except (OSError, ValueError):
            pass
    return connect_obs(OBS_CONFIG["host"], OBS_CONFIG["port"], password)


def obs_scenes():
    if OBS_CONFIG["mode"] in ("rtmp", "rtmp_push"):
        return {"connected": frame_ok(), "active": "Fluxo RTMP", "scenes": []}
    ws = obs_connection()
    try:
        data = obs_request(ws, "GetSceneList")
        return {
            "connected": True,
            "active": data.get("currentProgramSceneName"),
            "scenes": [item["sceneName"] for item in data.get("scenes", [])],
        }
    finally:
        ws.close()


def status():
    result = {
        "macIp": mac_ip(),
        "configuredIp": "192.168.1.10",
        "bridgeRunning": bridge_pid() is not None,
        "frameReady": frame_ok(),
        "phone": None,
        "obs": {"connected": False, "active": None, "scenes": []},
        "obsHost": OBS_CONFIG["host"],
        "obsPort": OBS_CONFIG["port"],
        "obsMode": OBS_CONFIG["mode"],
        "rtmpUrl": OBS_CONFIG["rtmpUrl"],
        "rtmpPublishServer": f"rtmp://{mac_ip()}:1935/camswap",
        "rtmpPublishKey": "live",
        "obsPasswordSaved": bool(OBS_CONFIG["password"]),
    }
    try:
        device = phone()
        params = device.query_system_parameters()
        result["phone"] = {
            "name": device.name,
            "id": device.id,
            "os": params.get("os", {}).get("version", "iOS"),
            "jailbreakReady": True,
            "tweakInstalled": None,
            "tweakVersion": None,
        }
        try:
            version = query_tweak_version(device)
            result["phone"]["tweakVersion"] = version
            result["phone"]["tweakInstalled"] = version is not None
        except Exception as exc:
            result["phone"]["tweakError"] = str(exc)
    except Exception as exc:
        result["phoneError"] = str(exc)
    try:
        result["obs"] = obs_scenes()
    except Exception as exc:
        result["obsError"] = str(exc)
    if OBS_CONFIG["mode"] == "rtmp" and not result["frameReady"]:
        parts = urlsplit(OBS_CONFIG["rtmpUrl"])
        try:
            with socket.create_connection((parts.hostname, parts.port or 1935), timeout=1):
                pass
        except OSError as exc:
            result["sourceError"] = f"{parts.hostname}:{parts.port or 1935} — {exc.strerror or exc}"
    return result


def apps():
    entries = phone().enumerate_applications(scope="full")
    result = []
    icons = {}
    for item in entries:
        available = item.parameters.get("icons", [])
        if available:
            icon = available[0]
            if icon.get("format") in ("png", "jpeg") and isinstance(icon.get("image"), bytes):
                icons[item.identifier] = (f"image/{icon['format']}", icon["image"])
        result.append({"name": item.name or item.identifier, "id": item.identifier, "icon": item.identifier in icons})
    with LOCK:
        ICON_CACHE.clear()
        ICON_CACHE.update(icons)
    return sorted(result, key=lambda item: item["name"].casefold())


def set_scene(name):
    if OBS_CONFIG["mode"] in ("rtmp", "rtmp_push"):
        raise ValueError("A troca de cenas exige o modo WebSocket")
    if not name or name not in obs_scenes()["scenes"]:
        raise ValueError("Cena não encontrada no OBS")
    ws = obs_connection()
    try:
        obs_request(ws, "SetCurrentProgramScene", {"sceneName": name})
    finally:
        ws.close()
    return {"scene": name}


def save_obs_config(data):
    global OBS_CONFIG
    mode = str(data.get("mode", "websocket"))
    if mode not in ("websocket", "rtmp", "rtmp_push"):
        raise ValueError("Modo de entrada inválido")
    host = str(data.get("host", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or host.startswith("-"):
        raise ValueError("Digite um IP ou nome de computador válido, sem http://")
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError):
        raise ValueError("Digite uma porta válida")
    if not 1 <= port <= 65535:
        raise ValueError("A porta deve estar entre 1 e 65535")
    password = str(data.get("password", ""))
    if not password and host == OBS_CONFIG["host"] and host not in ("127.0.0.1", "localhost"):
        password = OBS_CONFIG["password"]
    if len(password) > 256:
        raise ValueError("Senha muito longa")
    rtmp_url = str(data.get("rtmpUrl", "")).strip()
    if mode == "rtmp":
        parts = urlsplit(rtmp_url)
        if parts.scheme not in ("rtmp", "rtmps") or not parts.hostname or not parts.path:
            raise ValueError("Digite uma URL RTMP válida, incluindo o caminho do fluxo")
    with LOCK:
        OBS_CONFIG = {"mode": mode, "host": host, "port": port, "password": password, "rtmpUrl": rtmp_url}
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(SETTINGS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(OBS_CONFIG, file)
        os.chmod(SETTINGS_PATH, 0o600)
        was_running = bridge_pid() is not None
        if was_running:
            stop_bridge()
            start_bridge()
    return {"host": host, "port": port, "mode": mode, "bridgeRestarted": was_running}


def start_bridge():
    global BRIDGE
    with LOCK:
        pid = bridge_pid()
        if pid:
            return {"running": True, "pid": pid}
        script = ROOT / "obs_stream_server.py"
        if not script.exists():
            raise FileNotFoundError(f"Servidor não encontrado: {script}")
        env = os.environ.copy()
        if OBS_CONFIG["password"]:
            env["OBS_WEBSOCKET_PASSWORD"] = OBS_CONFIG["password"]
        if OBS_CONFIG["mode"] == "rtmp":
            env["OBS_RTMP_URL"] = OBS_CONFIG["rtmpUrl"]
        elif OBS_CONFIG["mode"] == "rtmp_push":
            env["OBS_RTMP_URL"] = "rtmp://0.0.0.0:1935/camswap/live"
            env["OBS_RTMP_LISTEN"] = "1"
        BRIDGE = subprocess.Popen(
            [sys.executable, str(script), "--obs-host", OBS_CONFIG["host"], "--obs-port", str(OBS_CONFIG["port"])], cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(30):
            pid = bridge_pid()
            if pid:
                return {"running": True, "pid": pid}
            if BRIDGE.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("A ponte não iniciou. Verifique se a porta 8080 está livre.")


def stop_bridge():
    with LOCK:
        pid = bridge_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(30):
                if bridge_pid() != pid:
                    break
                time.sleep(0.1)
        return {"running": False}


def newest_deb():
    files = list((ROOT / "packages").glob(f"{PACKAGE}_*_iphoneos-arm64.deb"))
    if not files:
        raise FileNotFoundError("Pacote .deb não encontrado. Execute ./build.sh.")
    return max(files, key=lambda p: p.stat().st_mtime)


def run_on_phone(device, path, argv, timeout=35):
    output = []

    def on_output(pid, fd, data):
        if data:
            output.append(data.decode("utf-8", "replace"))

    device.on("output", on_output)
    try:
        pid = device.spawn(path, argv=argv, env={"PATH": "/var/jb/usr/bin:/var/jb/bin:/usr/bin:/bin"}, stdio="pipe")
        device.resume(pid)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if all(process.pid != pid for process in device.enumerate_processes()):
                time.sleep(0.2)
                return "".join(output)
            time.sleep(0.2)
        raise TimeoutError("Comando no iPhone excedeu o tempo limite")
    finally:
        device.off("output", on_output)


def query_tweak_version(device):
    output = run_on_phone(
        device, "/var/jb/usr/bin/dpkg-query",
        ["dpkg-query", "-W", "-f=${Status}|${Version}\\n", PACKAGE], timeout=8,
    ).strip()
    match = re.search(r"install ok installed\|(\d+(?:\.\d+)+)", output)
    return match.group(1) if match else None


def transfer_deb(device, local_path):
    remote_path = "/var/mobile/Library/Caches/obs-iphone-studio.deb"
    payload = local_path.read_bytes()
    pid = device.spawn("/var/jb/usr/bin/sleep", argv=["sleep", "30"])
    session = device.attach(pid)
    done = threading.Event()
    response = {}
    script = session.create_script(r'''
const open = new NativeFunction(Module.getGlobalExportByName('open'), 'int', ['pointer', 'int', 'int']);
const write = new NativeFunction(Module.getGlobalExportByName('write'), 'int', ['int', 'pointer', 'ulong']);
const close = new NativeFunction(Module.getGlobalExportByName('close'), 'int', ['int']);
recv('deb', (message, data) => {
  try {
    const path = Memory.allocUtf8String(message.path);
    const fd = open(path, 0x601, 0o644);
    if (fd < 0) throw new Error('open falhou');
    const buf = Memory.alloc(data.byteLength);
    buf.writeByteArray(data);
    const count = write(fd, buf, data.byteLength);
    close(fd);
    if (count !== data.byteLength) throw new Error('write incompleto: ' + count);
    send({ok: true, count: count});
  } catch (e) { send({ok: false, error: String(e)}); }
});
''')

    def on_message(message, _data):
        if message.get("type") == "send":
            response.update(message["payload"])
            done.set()
        elif message.get("type") == "error":
            response.update(ok=False, error=message.get("stack", str(message)))
            done.set()

    try:
        script.on("message", on_message)
        script.load()
        device.resume(pid)
        script.post({"type": "deb", "path": remote_path}, payload)
        if not done.wait(10):
            raise TimeoutError("Envio do pacote para o iPhone expirou")
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Falha no envio do pacote"))
        return remote_path
    finally:
        try:
            session.detach()
        except Exception:
            pass
        try:
            device.kill(pid)
        except Exception:
            pass


def install():
    deb = newest_deb()
    device = phone()
    remote = transfer_deb(device, deb)
    output = run_on_phone(device, "/var/jb/usr/bin/dpkg", ["dpkg", "-i", remote])
    if "Setting up" not in output and "Configurando" not in output:
        raise RuntimeError(output.strip() or "dpkg não confirmou a instalação")
    run_on_phone(device, "/var/jb/usr/bin/killall", ["killall", "-9", "mediaserverd"], timeout=10)
    version = query_tweak_version(device)
    if version is None:
        raise RuntimeError("A instalação não apareceu no banco de pacotes do iPhone")
    return {"installed": True, "version": version, "package": deb.name, "log": output.strip()}


def uninstall():
    device = phone()
    output = run_on_phone(device, "/var/jb/usr/bin/dpkg", ["dpkg", "-r", PACKAGE])
    if "Removing" not in output and "Removendo" not in output:
        raise RuntimeError(output.strip() or "dpkg não confirmou a remoção")
    run_on_phone(device, "/var/jb/usr/bin/killall", ["killall", "-9", "mediaserverd"], timeout=10)
    if query_tweak_version(device) is not None:
        raise RuntimeError("O pacote ainda consta como instalado no iPhone")
    return {"installed": False, "log": output.strip()}


def screenshot():
    captures = ROOT / ".captures"
    captures.mkdir(exist_ok=True)
    target = captures / f"iphone-{int(time.time())}.png"
    result = subprocess.run(["idevicescreenshot", str(target)], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Falha na captura")
    subprocess.Popen(["open", str(target)])
    return {"path": str(target)}


class Handler(BaseHTTPRequestHandler):
    def icon(self):
        identifier = unquote(self.path[len("/icons/"):])
        with LOCK:
            icon = ICON_CACHE.get(identifier)
        if not icon:
            return self.respond(404, {"error": "Ícone não encontrado"})
        content_type, body = icon
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, method):
        try:
            if method == "GET":
                if self.path.startswith("/icons/"):
                    return self.icon()
                routes = {"/status": status, "/apps": apps}
                if self.path not in routes:
                    return self.respond(404, {"error": "Rota desconhecida"})
                data = routes[self.path]()
                return self.respond(200, {"apps": data} if self.path == "/apps" else data)
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                return self.respond(413, {"error": "Pedido muito grande"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            routes = {
                "/obs/config": lambda: save_obs_config(payload),
                "/scene": lambda: set_scene(payload.get("name")),
                "/bridge/start": start_bridge,
                "/bridge/stop": stop_bridge,
                "/tweak/install": install,
                "/tweak/uninstall": uninstall,
                "/screenshot": screenshot,
            }
            if self.path not in routes:
                return self.respond(404, {"error": "Rota desconhecida"})
            return self.respond(200, routes[self.path]())
        except Exception as exc:
            return self.respond(500, {"error": str(exc)})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def log_message(self, *_args):
        pass


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    ROOT = args.root.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Painel API em 127.0.0.1:{args.port}", flush=True)
    try:
        start_bridge()
    except Exception as exc:
        print(f"Ponte não iniciada automaticamente: {exc}", flush=True)
    def shutdown(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if BRIDGE and BRIDGE.poll() is None:
            stop_bridge()


if __name__ == "__main__":
    main()
