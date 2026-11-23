"""独立可信执行器。共享目录传递固定请求和回执，实验容器只收到脚本与标准输入。"""

import argparse
import fcntl
import hashlib
import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from uuid import UUID

IMAGE = "ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"
FIXTURE = Path(__file__).with_name("reranker_check.py")
LABEL = "com.knowledgeops.research-attempt"
MAX_INPUT = 128 * 1024
MAX_OUTPUT = 64 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def validate(request):
    attempt = request["attempt_id"]
    if str(UUID(attempt)) != attempt:
        raise ValueError("invalid attempt identity")
    if request["kind"] != "reranker-check-v1" or request["image"] != IMAGE:
        raise ValueError("experiment fixture or image has not been approved")
    if type(request["timeout_seconds"]) is not int or not 1 <= request["timeout_seconds"] <= 60:
        raise ValueError("invalid timeout")
    payload = canonical(request["input"])
    if len(payload) > MAX_INPUT or digest(payload) != request["input_sha256"]:
        raise ValueError("input budget or digest mismatch")
    if digest(FIXTURE.read_bytes()) != request["script_sha256"]:
        raise ValueError("fixture script digest mismatch")


def path(exchange, folder, attempt):
    return exchange / folder / f"{attempt}.json"


def install(target, value):
    """同目录原子发布且不覆盖；重投保留已有领取标记与结果。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent) as temporary:
        temporary.write(canonical(value))
        temporary.flush()
        os.fsync(temporary.fileno())
        try:
            os.link(temporary.name, target)
        except FileExistsError:
            return False
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def handle(exchange, request, *, execute=None):
    """持有独占执行器租约时处理一个请求。断在领取后则登记未知，不能重放容器。"""
    attempt = request["attempt_id"]
    if str(UUID(attempt)) != attempt:
        raise ValueError("invalid attempt identity")
    identity = digest(canonical(request))
    target = path(exchange, "results", attempt)
    if target.exists():
        saved = json.loads(target.read_bytes())
        if saved["request_sha256"] != identity:
            raise ValueError("attempt identifier was already used for another request")
        return saved
    validate(request)
    marker = path(exchange, "claims", attempt)
    cancelled = path(exchange, "cancel", attempt).exists
    if marker.exists():
        outcome = {"status": "unknown", "reason": "领取记录存在，但执行器未写回结果。"}
    elif cancelled():
        outcome = {"status": "cancelled", "reason": "执行前已收到取消。"}
    elif install(marker, {"request_sha256": identity, "claimed_at": time.time()}):
        if cancelled():
            outcome = {"status": "cancelled", "reason": "启动容器前已收到取消。"}
        else:
            outcome = (execute or execute_sandbox)(request, cancelled)
    else:
        raise RuntimeError("another runner owns this exchange")
    receipt = {
        **outcome, "attempt_id": attempt, "request_sha256": identity, "image": request["image"],
        "input_sha256": request["input_sha256"], "script_sha256": request["script_sha256"],
    }
    if not install(target, receipt):
        raise RuntimeError("result was written by another runner")
    return receipt


def stop_sandbox(name, attempt):
    """只停止具有本次精确身份标签的容器。引擎无法确认时保留 unknown。"""
    try:
        inspected = subprocess.run(
            ["docker", "container", "inspect", name, "--format", '{{json .Config.Labels}}'],
            capture_output=True, timeout=4, check=False,
        )
        if inspected.returncode:
            return b"No such container" in inspected.stderr or b"No such object" in inspected.stderr
        if (json.loads(inspected.stdout) or {}).get(LABEL) != attempt:
            return False
        removed = subprocess.run(["docker", "container", "rm", "--force", name],
                                 capture_output=True, timeout=5, check=False)
        return removed.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def execute_sandbox(request, cancelled):
    """Docker CLI 只在这个可信执行器进程运行；内层容器没有挂载、网络和应用凭据。"""
    attempt = request["attempt_id"]
    name = "knowledgeops-exp-" + attempt
    seconds = request["timeout_seconds"]
    # 容器端的闹钟即使执行器意外退出仍生效；父进程另有独立的启动与输出期限。
    trusted = FIXTURE.read_text(encoding="utf-8")
    program = (
        "import signal\n"
        "def stop_at_deadline(*_):\n    raise SystemExit(124)\n"
        "signal.signal(signal.SIGALRM, stop_at_deadline)\n"
        f"signal.alarm({seconds})\n"
        + trusted
    )
    argv = [
        "docker", "run", "--rm", "--pull=never", "-i", "--name", name,
        "--label", f"{LABEL}={attempt}", "--network=none", "--read-only", "--user=65534:65534",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=96m", "--memory-swap=96m",
        "--cpus=0.5", "--pids-limit=32", "--log-driver=none",
        "--entrypoint=/usr/local/bin/python", IMAGE, "-I", "-c", program,
    ]
    with TemporaryFile() as data:
        data.write(canonical(request["input"]))
        data.seek(0)
        try:
            child = subprocess.Popen(argv, stdin=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            return {"status": "failed", "reason": "独立执行器无法启动容器客户端。"}
        with child, selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            output = bytearray()
            observed = 0
            deadline = time.monotonic() + seconds + 3
            stop = None
            while selector.get_map() or child.poll() is None:
                if cancelled():
                    stop = ("cancelled", "执行已撤回。")
                    break
                if time.monotonic() >= deadline:
                    stop = ("timed_out", "实验超过时间上限。")
                    break
                ready = selector.select(timeout=0.15)
                for stream, _ in ready:
                    chunk = os.read(stream.fd, 4096)
                    if not chunk:
                        selector.unregister(stream.fd)
                        continue
                    observed += len(chunk)
                    if stream.data == "stdout" and observed <= MAX_OUTPUT:
                        output.extend(chunk)
                if observed > MAX_OUTPUT:
                    stop = ("failed", "实验输出超过限额。")
                    break
            if stop:
                if child.poll() is None:
                    child.kill()
                child.wait()
                status, reason = stop if stop_sandbox(name, attempt) else (
                    "unknown", "未能确认实验容器已停止。"
                )
                return {"status": status, "reason": reason}
            exit_code = child.wait()
    if exit_code != 0:
        status = "timed_out" if exit_code in {124, 142} else "unknown" if exit_code == 125 else "failed"
        return {"status": status, "exit_code": exit_code, "reason": "实验进程未正常返回结果。"}
    try:
        result = json.loads(output)
        if not isinstance(result, dict) or result.get("schema_version") != 1 or not isinstance(result.get("cases"), list):
            raise ValueError("invalid result")
    except (UnicodeDecodeError, ValueError):
        return {"status": "failed", "exit_code": exit_code, "reason": "实验结果格式不符合约定。"}
    return {"status": "completed", "exit_code": exit_code, "output": result}


def run_pending(exchange, *, watch=False):
    exchange.mkdir(parents=True, exist_ok=True)
    with (exchange / "executor.lock").open("a+b") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            scan(exchange)
            if not watch:
                return
            time.sleep(0.5)


def scan(exchange):
    for candidate in sorted((exchange / "requests").glob("*.json")):
        # 读取经过原子安装的有界协议消息；不跟随目录内符号链接。
        if candidate.is_symlink() or candidate.stat().st_size > MAX_INPUT + 4096:
            continue
        try:
            request = json.loads(candidate.read_bytes())
            if candidate.stem != request["attempt_id"]:
                raise ValueError("request path identity mismatch")
            handle(exchange, request)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            print(f"attempt {candidate.stem[:36]} rejected: {type(error).__name__}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exchange", type=Path)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    run_pending(args.exchange.resolve(), watch=args.watch)
