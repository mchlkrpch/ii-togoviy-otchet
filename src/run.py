#!/usr/bin/env python3
"""Prepare local dependencies and start the report verification application."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser


BASE = Path(__file__).resolve().parent


def run(command, directory=BASE):
    print("→ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=directory, check=True)


def main():
    parser = argparse.ArgumentParser(description="Локальное рабочее место проверки отчетности")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.exit(1, "Нужен Python 3.11 или новее.\n")
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            state = json.load(response)
            if state.get("application") == "report-verification":
                if state.get("structure_slice") is not True:
                    parser.exit(
                        1,
                        f"На {url} запущена старая версия сервера без актуальных инструментов разметки. "
                        "Остановите её (Ctrl+C в терминале запуска) и запустите приложение снова.\n",
                    )
                print(f"Приложение уже запущено: {url}")
                if not args.no_browser:
                    webbrowser.open(url)
                return
    except (OSError, ValueError):
        pass
    environment = BASE / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Создаю локальное Python-окружение…", flush=True)
        venv.create(environment, with_pip=True)
    requirements = BASE / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / ".requirements-sha256"
    if not marker.exists() or marker.read_text() != fingerprint:
        run([python, "-m", "pip", "install", "-r", requirements])
        marker.write_text(fingerprint)
    frontend = BASE / "frontend"
    source_paths = [frontend / "package.json", frontend / "package-lock.json", frontend / "index.html", frontend / "vite.config.ts", *list((frontend / "src").rglob("*"))]
    built = frontend / "dist" / "index.html"
    needs_build = not built.exists() or any(path.is_file() and path.stat().st_mtime > built.stat().st_mtime for path in source_paths)
    if needs_build:
        npm = shutil.which("npm")
        if not npm:
            parser.exit(1, "Для первой сборки нужен Node.js 22 LTS с npm. Также доступен запуск через Docker Compose.\n")
        install_fingerprint = hashlib.sha256((frontend / "package.json").read_bytes() + (frontend / "package-lock.json").read_bytes()).hexdigest()
        install_marker = frontend / "node_modules" / ".install-sha256"
        if not install_marker.exists() or install_marker.read_text() != install_fingerprint:
            run([npm, "ci", "--no-audit", "--no-fund"], frontend)
            install_marker.write_text(install_fingerprint)
        run([npm, "run", "build"], frontend)
    if not shutil.which("tesseract"):
        print("Для сканов установите Tesseract с языками rus и eng. На macOS: brew install tesseract tesseract-lang", flush=True)
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError as exc:
            parser.exit(1, f"Не удалось открыть порт {args.port}: {exc}. При занятом порте укажите другой через --port.\n")
    print(f"\nПроверка отчетности: {url}\nДля остановки нажмите Ctrl+C.\n", flush=True)
    server = subprocess.Popen([str(python), "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(args.port)], cwd=BASE)
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError("Сервер завершился во время запуска")
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Сервер не ответил за время ожидания")
        if not args.no_browser:
            webbrowser.open(url)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Не удалось запустить приложение: {exc}", file=sys.stderr)
        sys.exit(1)
