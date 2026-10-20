"""Ten owned, isolated VOICEVOX CPU engines. No GPU or shared-engine requests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

WORKERS = 10


def engine_fingerprint(directory, cache=None):
    directory = Path(directory)
    paths = [directory / "run", directory / "libvoicevox_core.so",
             directory / "libvoicevox_onnxruntime.so", directory / "engine_manifest.json"]
    paths += sorted((directory / "model").rglob("*"))
    paths = [path for path in paths if path.is_file()]
    signature = []
    for path in paths:
        stat = path.stat()
        signature.append([str(path.relative_to(directory)), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    if cache is not None and Path(cache).is_file():
        previous = json.loads(Path(cache).read_text())
        if previous.get("signature") == signature and previous.get("directory") == str(directory.resolve()):
            return previous["sha256"]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(directory)).encode())
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    value = digest.hexdigest()
    if cache is not None:
        cache = Path(cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(".tmp")
        temp.write_text(json.dumps({"directory": str(directory.resolve()), "signature": signature, "sha256": value}))
        temp.replace(cache)
    return value


class EnginePool:
    def __init__(self, directory, logs, base_port=50131, timeout=180):
        self.directory = Path(directory).resolve()
        self.logs = Path(logs)
        self.base_port = base_port
        self.timeout = timeout
        self.processes = []
        self.handles = []
        self.versions = []
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.events = []
        self.report = {"device": "cpu", "workers": WORKERS,
                       "cpu_threads_per_engine": 1, "gpu_used": False}

    def request(self, worker, route, parameters=None, body=None):
        url = f"http://127.0.0.1:{self.base_port + worker}/{route}"
        if parameters:
            url += "?" + urllib.parse.urlencode(parameters)
        request = urllib.request.Request(url, data=body)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=self.timeout) as response:
            return response.read()

    def _ready(self, worker):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            process = self.processes[worker]
            if process.poll() is not None:
                tail = (self.logs / f"engine-{worker:02d}.log").read_text(errors="replace")[-800:]
                raise RuntimeError(f"CPU engine {worker} exited: {tail}")
            try:
                return json.loads(self.request(worker, "version"))
            except (OSError, ValueError):
                time.sleep(0.15)
        raise TimeoutError(f"CPU engine {worker} did not start")

    def __enter__(self):
        started = time.monotonic()
        self.logs.mkdir(parents=True, exist_ok=True)
        # Do not attach to or terminate an existing service at these ports.
        reservations = []
        try:
            for worker in range(WORKERS):
                sock = socket.socket()
                reservations.append(sock)
                sock.bind(("127.0.0.1", self.base_port + worker))
        finally:
            for sock in reservations:
                sock.close()
        environment = dict(os.environ, CUDA_VISIBLE_DEVICES="-1", HIP_VISIBLE_DEVICES="-1",
                           OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                           MKL_NUM_THREADS="1", VV_CPU_NUM_THREADS="1")
        try:
            for worker in range(WORKERS):
                command = [str(self.directory / "run"), "--host", "127.0.0.1",
                           "--port", str(self.base_port + worker),
                           "--voicevox_dir", str(self.directory),
                           "--cpu_num_threads", "1", "--disable_mutable_api", "--output_log_utf8"]
                handle = (self.logs / f"engine-{worker:02d}.log").open("wb")
                self.handles.append(handle)
                self.processes.append(subprocess.Popen(command, cwd=self.directory,
                    env=environment, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True))
            with ThreadPoolExecutor(max_workers=WORKERS) as executor:
                self.versions = list(executor.map(self._ready, range(WORKERS)))
            if len(set(self.versions)) != 1:
                raise RuntimeError("Engine versions differ")
            speakers = json.loads(self.request(0, "speakers"))
            actual = {style["id"]: speaker["name"] for speaker in speakers for style in speaker["styles"]}
            if actual.get(3) != "ずんだもん" or actual.get(2) != "四国めたん":
                raise RuntimeError("Expected Zundamon style3 / Shikoku Metan style2")
            self.report.update(engine_version=self.versions[0],
                               engine_ready_seconds=round(time.monotonic() - started, 3),
                               pids=[p.pid for p in self.processes],
                               ports=list(range(self.base_port, self.base_port + WORKERS)))
            return self
        except BaseException:
            self.close()
            raise

    def synthesize(self, worker, text, speaker, speed=1.10):
        query = json.loads(self.request(worker, "audio_query", {"text": text, "speaker": speaker}, b""))
        query["speedScale"] = speed
        started = time.monotonic()
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            audio = self.request(worker, "synthesis", {"speaker": speaker},
                                 json.dumps(query, ensure_ascii=False).encode())
            return audio, query
        finally:
            with self.lock:
                self.inflight -= 1
                self.events.append({"worker": worker, "port": self.base_port + worker,
                                    "speaker_id": speaker, "start_monotonic": started,
                                    "end_monotonic": time.monotonic()})

    def close(self):
        started = time.monotonic()
        for process in self.processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 10
        for process in self.processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for handle in self.handles:
            handle.close()
        self.report.update(max_parallel_synthesis=self.max_inflight,
                           synthesis_requests=self.events,
                           owned_engines_stopped=all(p.poll() is not None for p in self.processes),
                           cleanup_seconds=round(time.monotonic() - started, 3))

    def __exit__(self, *exc):
        self.close()
