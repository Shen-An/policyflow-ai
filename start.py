"""One-command launcher for PolicyFlow AI backend and frontend."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_ROOT / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"


def ensure_policyflow_runtime() -> None:
    """Fail fast when the process is not the conda policyflow env with LightRAG."""
    missing: list[str] = []
    try:
        import lightrag  # noqa: F401
    except ImportError:
        missing.append("lightrag (package: lightrag-hku)")
    try:
        import rank_bm25  # noqa: F401
    except ImportError:
        missing.append("rank_bm25")
    if missing:
        raise RuntimeError(
            "当前 Python 缺少 RAG 依赖："
            + "、".join(missing)
            + f"。\n  解释器：{sys.executable}\n"
            "请先激活正确环境再启动：\n"
            "  conda activate policyflow\n"
            "  python start.py\n"
            "或：\n"
            "  E:\\Coding\\Anaconda\\envs\\policyflow\\python.exe start.py\n"
            "用 base/Anaconda 根环境启动会导致文档索引全部 failed（No module named 'lightrag'）。"
        )
    # Soft hint when not clearly the policyflow env, but deps are present.
    exe = sys.executable.replace("\\", "/").lower()
    if "policyflow" not in exe and "envs/" not in exe:
        print(
            f"[PolicyFlow] 警告：当前解释器可能不是 conda policyflow 环境：{sys.executable}",
            file=sys.stderr,
        )


def npm_command() -> str:
    command = "npm.cmd" if os.name == "nt" else "npm"
    resolved = shutil.which(command)
    if resolved is None:
        raise RuntimeError("未找到 npm。请先安装 Node.js，或使用 --no-build 启动已有前端产物。")
    return resolved


def node_command() -> str:
    resolved = shutil.which("node.exe" if os.name == "nt" else "node")
    if resolved is None:
        raise RuntimeError("未找到 Node.js。请先安装 Node.js。")
    return resolved


def frontend_needs_build() -> bool:
    if not FRONTEND_INDEX.is_file():
        return True
    output_time = FRONTEND_INDEX.stat().st_mtime
    watched = [
        FRONTEND_ROOT / "src",
        FRONTEND_ROOT / "index.html",
        FRONTEND_ROOT / "package.json",
        FRONTEND_ROOT / "package-lock.json",
        FRONTEND_ROOT / "vite.config.ts",
    ]
    for item in watched:
        if item.is_file() and item.stat().st_mtime > output_time:
            return True
        if item.is_dir() and any(
            path.is_file() and path.stat().st_mtime > output_time
            for path in item.rglob("*")
        ):
            return True
    return False


def build_frontend(force: bool = False) -> None:
    if not force and not frontend_needs_build():
        print("[PolicyFlow] 前端产物已是最新，跳过构建。")
        return
    print("[PolicyFlow] 正在构建前端……")
    subprocess.run([npm_command(), "run", "build"], cwd=FRONTEND_ROOT, check=True)


def open_when_ready(url: str) -> None:
    def worker() -> None:
        for _ in range(120):
            try:
                with urllib.request.urlopen(url, timeout=1):
                    webbrowser.open(url)
                    return
            except Exception:
                time.sleep(0.25)

    threading.Thread(target=worker, daemon=True).start()


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run_development(host: str, port: int, open_browser: bool) -> int:
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
    )
    frontend = subprocess.Popen(
        [
            node_command(),
            str(FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"),
            "--host",
            "127.0.0.1",
            "--strictPort",
        ],
        cwd=FRONTEND_ROOT,
    )
    if open_browser:
        open_when_ready("http://127.0.0.1:5173")
    print(f"[PolicyFlow] 开发模式：前端 http://127.0.0.1:5173，后端 http://127.0.0.1:{port}")
    try:
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[PolicyFlow] 正在停止开发服务……")
    finally:
        terminate(frontend)
        terminate(backend)
    return backend.returncode or frontend.returncode or 0


def run_integrated(host: str, port: int, open_browser: bool) -> int:
    import uvicorn

    from backend.app.main import DEFAULT_FRONTEND_DIST, create_app

    application = create_app(frontend_dist=DEFAULT_FRONTEND_DIST)
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        open_when_ready(url)
    print(f"[PolicyFlow] 已启动：{url}")
    uvicorn.run(application, host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 PolicyFlow AI 前后端")
    parser.add_argument("--dev", action="store_true", help="同时启动 Vite 热更新和 FastAPI")
    parser.add_argument("--host", default="0.0.0.0", help="后端监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="统一服务端口，默认 8000")
    parser.add_argument("--no-build", action="store_true", help="不检查或构建前端")
    parser.add_argument("--rebuild", action="store_true", help="强制重新构建前端")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_policyflow_runtime()
    if not args.dev and not args.no_build:
        build_frontend(force=args.rebuild)
    if not args.dev and not FRONTEND_INDEX.is_file():
        raise RuntimeError("前端 dist 不存在。请移除 --no-build，或先执行 frontend/npm run build。")
    if args.dev:
        return run_development(args.host, args.port, not args.no_browser)
    return run_integrated(args.host, args.port, not args.no_browser)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[PolicyFlow] 启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
