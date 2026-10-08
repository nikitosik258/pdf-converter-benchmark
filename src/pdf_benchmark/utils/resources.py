from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import psutil

from pdf_benchmark.models import ResourceUsage


@dataclass
class _NVML:
    module: object | None = None
    handles: list[object] | None = None


def _try_nvml() -> _NVML:
    try:
        import pynvml  # provided by nvidia-ml-py
        pynvml.nvmlInit()
        handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]
        return _NVML(pynvml, handles)
    except Exception:
        return _NVML()


def _process_tree(proc: psutil.Process) -> list[psutil.Process]:
    out = [proc]
    try:
        out.extend(proc.children(recursive=True))
    except Exception:
        pass
    return out


class ResourceMonitor:
    """Best-effort resource monitor for an adapter invocation and its child processes."""

    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.proc = psutil.Process(os.getpid())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._nvml = _try_nvml()
        self._peak_rss = 0
        self._peak_tree_rss = 0
        self._peak_gpu_proc = 0
        self._peak_gpu_device = 0
        self._start_wall = 0.0
        self._cpu_baseline: dict[int, float] = {}
        self._cpu_last: dict[int, float] = {}
        self._torch_available = False

    def __enter__(self):
        self._start_wall = time.perf_counter()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
                self._torch_available = True
        except Exception:
            pass
        self._sample_once()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        return self

    def _sample_once(self):
        try:
            tree = _process_tree(self.proc)
            rss_vals: list[int] = []
            pids: set[int] = set()
            for p in tree:
                try:
                    rss_vals.append(p.memory_info().rss)
                    pids.add(p.pid)
                    ct = p.cpu_times()
                    total_cpu = float(ct.user + ct.system)
                    self._cpu_baseline.setdefault(p.pid, total_cpu)
                    self._cpu_last[p.pid] = total_cpu
                except Exception:
                    pass
            if rss_vals:
                self._peak_rss = max(self._peak_rss, rss_vals[0])
                self._peak_tree_rss = max(self._peak_tree_rss, sum(rss_vals))

            if self._nvml.module and self._nvml.handles:
                nv = self._nvml.module
                proc_mem = 0
                device_mem = 0
                for h in self._nvml.handles:
                    try:
                        device_mem += int(nv.nvmlDeviceGetMemoryInfo(h).used)
                    except Exception:
                        pass
                    for fn_name in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
                        try:
                            for gp in getattr(nv, fn_name)(h):
                                if gp.pid in pids and getattr(gp, "usedGpuMemory", None):
                                    proc_mem += int(gp.usedGpuMemory)
                        except Exception:
                            pass
                self._peak_gpu_proc = max(self._peak_gpu_proc, proc_mem)
                self._peak_gpu_device = max(self._peak_gpu_device, device_mem)
        except Exception:
            pass

    def _sample_loop(self):
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval)

    def __exit__(self, exc_type, exc, tb):
        self._sample_once()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._finished = time.perf_counter()
        try:
            if self._nvml.module:
                self._nvml.module.nvmlShutdown()
        except Exception:
            pass

    def result(self) -> ResourceUsage:
        wall = max(0.0, getattr(self, "_finished", time.perf_counter()) - self._start_wall)
        cpu = sum(max(0.0, self._cpu_last.get(pid, start) - start) for pid, start in self._cpu_baseline.items())
        cpu_count = psutil.cpu_count(logical=True) or 1
        avg_cpu = (cpu / wall / cpu_count * 100.0) if wall > 0 else None
        torch_alloc = None
        torch_reserved = None
        if self._torch_available:
            try:
                import torch
                torch_alloc = torch.cuda.max_memory_allocated() / (1024**2)
                torch_reserved = torch.cuda.max_memory_reserved() / (1024**2)
            except Exception:
                pass
        return ResourceUsage(
            wall_time_seconds=wall,
            cpu_time_seconds=cpu,
            average_cpu_percent_of_machine=avg_cpu,
            peak_rss_mb=self._peak_rss / (1024**2) if self._peak_rss else None,
            peak_process_tree_rss_mb=self._peak_tree_rss / (1024**2) if self._peak_tree_rss else None,
            gpu_peak_process_mb=self._peak_gpu_proc / (1024**2) if self._peak_gpu_proc else None,
            gpu_peak_device_used_mb=self._peak_gpu_device / (1024**2) if self._peak_gpu_device else None,
            torch_peak_allocated_mb=torch_alloc,
            torch_peak_reserved_mb=torch_reserved,
        )
