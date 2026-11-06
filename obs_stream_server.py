#!/usr/bin/env python3
"""Expose the current OBS program scene as a JPEG frame for the iPhone tweak."""

import argparse
import base64
import hashlib
import json
import logging
import os
import signal
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import websocket

RTMP_CHILD = None


class FrameStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.updated = 0.0

    def put(self, frame):
        with self.lock:
            self.frame = frame
            self.updated = time.monotonic()

    def get(self, max_age):
        with self.lock:
            if self.frame and time.monotonic() - self.updated <= max_age:
                return self.frame
            return None


def obs_request(ws, request_type, data=None):
    request_id = str(uuid.uuid4())
    ws.send(json.dumps({"op": 6, "d": {
        "requestType": request_type,
        "requestId": request_id,
        "requestData": data or {},
    }}))
    while True:
        message = json.loads(ws.recv())
        if message.get("op") != 7:
            continue
        response = message["d"]
        if response.get("requestId") != request_id:
            continue
        status = response.get("requestStatus", {})
        if not status.get("result"):
            raise RuntimeError(f"OBS {request_type}: {status.get('comment', status)}")
        return response.get("responseData", {})


def connect_obs(host, port, password):
    ws = websocket.create_connection(f"ws://{host}:{port}", timeout=5)
    try:
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            raise RuntimeError("Resposta inicial inesperada do OBS")
        identify = {"rpcVersion": 1}
        auth = hello["d"].get("authentication")
        if auth:
            if not password:
                raise RuntimeError("O OBS exige senha; use --password")
            secret = base64.b64encode(hashlib.sha256(
                (password + auth["salt"]).encode()).digest()).decode()
            identify["authentication"] = base64.b64encode(hashlib.sha256(
                (secret + auth["challenge"]).encode()).digest()).decode()
        ws.send(json.dumps({"op": 1, "d": identify}))
        identified = json.loads(ws.recv())
        if identified.get("op") != 2:
            raise RuntimeError("Autenticação WebSocket recusada pelo OBS")
        return ws
    except Exception:
        ws.close()
        raise


def capture_loop(args, frames):
    delay = 1.0 / args.fps
    while True:
        ws = None
        try:
            ws = connect_obs(args.obs_host, args.obs_port, args.password)
            logging.info("Conectado ao OBS")
            while True:
                start = time.monotonic()
                source = args.source or obs_request(ws, "GetCurrentProgramScene")["currentProgramSceneName"]
                result = obs_request(ws, "GetSourceScreenshot", {
                    "sourceName": source,
                    "imageFormat": "jpg",
                    "imageWidth": args.width,
                    "imageCompressionQuality": args.quality,
                })
                image_data = result["imageData"].partition(",")[2]
                jpeg = base64.b64decode(image_data, validate=True)
                if not jpeg.startswith(b"\xff\xd8"):
                    raise RuntimeError("O OBS não retornou um JPEG válido")
                frames.put(jpeg)
                time.sleep(max(0, delay - (time.monotonic() - start)))
        except (Exception, KeyboardInterrupt) as exc:
            logging.warning("OBS indisponível: %s; tentando novamente em 2 s", exc)
            time.sleep(2)
        finally:
            if ws:
                ws.close()


def rtmp_capture_loop(args, frames):
    """Decode an RTMP stream to JPEG frames using FFmpeg, restarting on disconnect."""
    global RTMP_CHILD
    while True:
        child = None
        try:
            ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-fflags", "nobuffer", "-flags", "low_delay",
            ]
            if args.rtmp_listen:
                command += ["-listen", "1"]
            command += [
                "-i", args.rtmp_url, "-an",
                "-vf", f"fps={args.fps},scale={args.width}:-2",
                "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
            ]
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            RTMP_CHILD = child
            logging.info("Lendo RTMP de %s", args.rtmp_url)
            pending = bytearray()
            while True:
                chunk = child.stdout.read(16384)
                if not chunk:
                    break
                pending.extend(chunk)
                while True:
                    start = pending.find(b"\xff\xd8")
                    if start < 0:
                        pending.clear()
                        break
                    end = pending.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start:
                            del pending[:start]
                        break
                    frames.put(bytes(pending[start:end + 2]))
                    del pending[:end + 2]
                if len(pending) > 8_000_000:
                    pending.clear()
            logging.warning("Fluxo RTMP terminou; tentando novamente em 2 s")
        except Exception as exc:
            logging.warning("RTMP indisponível: %s; tentando novamente em 2 s", exc)
        finally:
            if child:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
            RTMP_CHILD = None
        time.sleep(2)


def handler_for(frames, max_age):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path == "/":
                body = ("<!doctype html><html lang='pt-br'><meta name='viewport' "
                        "content='width=device-width, initial-scale=1'><title>OBS Camera Bridge</title>"
                        "<style>body{background:#111;color:#eee;font:16px sans-serif;text-align:center}"
                        "img{max-width:100%;height:auto}</style><h1>OBS → iPhone</h1>"
                        "<p>Prévia ao vivo da cena do OBS</p><img id='preview' src='/frame.jpg'>"
                        "<script>setInterval(()=>{document.getElementById('preview').src="
                        "'/frame.jpg?t='+Date.now()},500)</script></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path not in ("/frame.jpg", "/health"):
                self.send_error(404)
                return
            frame = frames.get(max_age)
            if frame is None:
                self.send_error(503, "Sem quadro recente do OBS")
                return
            body = frame if path == "/frame.jpg" else b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if path == "/frame.jpg" else "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            if args and args[1] != "200":
                logging.info("HTTP %s", format % args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs-host", default="127.0.0.1")
    parser.add_argument("--obs-port", type=int, default=4455)
    parser.add_argument("--password", help="Senha do WebSocket; por padrão, lê a configuração local do OBS")
    parser.add_argument("--source", help="Nome de uma cena ou fonte; padrão: cena ao vivo")
    parser.add_argument("--rtmp-url", help="Fluxo RTMP alternativo ao WebSocket do OBS")
    parser.add_argument("--rtmp-listen", action="store_true", help="Receber publicação RTMP neste Mac")
    parser.add_argument("--host", default="0.0.0.0", help="Interface HTTP para o iPhone")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--fps", type=float, default=12)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--quality", type=int, default=85)
    args = parser.parse_args()
    args.rtmp_url = args.rtmp_url or os.environ.get("OBS_RTMP_URL")
    args.rtmp_listen = args.rtmp_listen or os.environ.get("OBS_RTMP_LISTEN") == "1"
    if args.password is None:
        args.password = os.environ.get("OBS_WEBSOCKET_PASSWORD")
    if args.password is None:
        config_path = Path.home() / "Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
        try:
            args.password = json.loads(config_path.read_text()).get("server_password", "")
        except (OSError, ValueError):
            args.password = ""
    if not 1 <= args.fps <= 15 or not 8 <= args.width <= 4096 or not 0 <= args.quality <= 100:
        parser.error("Use FPS de 1 a 15, largura de 8 a 4096 e qualidade de 0 a 100")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    frames = FrameStore()
    target = rtmp_capture_loop if args.rtmp_url else capture_loop
    threading.Thread(target=target, args=(args, frames), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(frames, max_age=2.0))
    logging.info("Quadros disponíveis em http://<IP-do-Mac>:%d/frame.jpg", args.port)
    def shutdown(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if RTMP_CHILD and RTMP_CHILD.poll() is None:
            RTMP_CHILD.terminate()


if __name__ == "__main__":
    main()
