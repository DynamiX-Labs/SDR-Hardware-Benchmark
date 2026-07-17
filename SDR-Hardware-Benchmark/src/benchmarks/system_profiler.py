"""
System Profiler — Hardware & Software Platform Detection
Captures CPU, memory, GPU, and numeric library capabilities.

Inspired by:
  - YAMCS: System-aware service architecture
  - SatDump: Platform-aware benchmark configuration

DynamiX Labs
"""

import platform
import sys
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("benchmark.profiler")


@dataclass
class CPUInfo:
    model: str = "Unknown"
    architecture: str = ""
    cores_physical: int = 0
    cores_logical: int = 0
    frequency_mhz: float = 0.0
    cache_l2_kb: int = 0
    simd_extensions: List[str] = field(default_factory=list)


@dataclass
class MemoryInfo:
    total_gb: float = 0.0
    available_gb: float = 0.0
    swap_total_gb: float = 0.0


@dataclass
class GPUInfo:
    available: bool = False
    name: str = "None"
    vram_gb: float = 0.0
    cuda_version: str = ""
    cupy_available: bool = False


@dataclass
class NumericBackend:
    numpy_version: str = ""
    scipy_version: str = ""
    blas_library: str = "Unknown"
    lapack_library: str = "Unknown"
    threading: str = ""


@dataclass
class SystemProfile:
    """Complete system profile for benchmark context."""
    hostname: str = ""
    os_name: str = ""
    os_version: str = ""
    python_version: str = ""
    cpu: CPUInfo = field(default_factory=CPUInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    gpu: GPUInfo = field(default_factory=GPUInfo)
    numeric: NumericBackend = field(default_factory=NumericBackend)
    timestamp: str = ""

    def summary(self) -> str:
        lines = [
            "-" * 60,
            "              SYSTEM PROFILE                              ",
            "-" * 60,
            f" Host:     {self.hostname:<46} ",
            f" OS:       {self.os_name} {self.os_version:<36} ",
            f" Python:   {self.python_version:<46} ",
            "-" * 60,
            f" CPU:      {self.cpu.model[:46]:<46} ",
            f" Cores:    {self.cpu.cores_physical}P / {self.cpu.cores_logical}L"
            f"{'':>{40 - len(str(self.cpu.cores_physical)) - len(str(self.cpu.cores_logical))}} ",
            f" Freq:     {self.cpu.frequency_mhz:.0f} MHz"
            f"{'':>{38 - len(f'{self.cpu.frequency_mhz:.0f}')}} ",
        ]
        if self.cpu.simd_extensions:
            simd_str = ", ".join(self.cpu.simd_extensions[:6])
            lines.append(f" SIMD:     {simd_str[:46]:<46} ")
        lines.extend([
            "-" * 60,
            f" RAM:      {self.memory.total_gb:.1f} GB total, "
            f"{self.memory.available_gb:.1f} GB free"
            f"{'':>{24 - len(f'{self.memory.total_gb:.1f}') - len(f'{self.memory.available_gb:.1f}')}} ",
        ])
        if self.gpu.available:
            lines.extend([
                "-" * 60,
                f" GPU:      {self.gpu.name[:46]:<46} ",
                f" VRAM:     {self.gpu.vram_gb:.1f} GB"
                f"{'':>{40 - len(f'{self.gpu.vram_gb:.1f}')}} ",
                f" CUDA:     {self.gpu.cuda_version:<46} ",
            ])
        else:
            lines.append(" GPU:      Not detected                                  ")
        lines.extend([
            "-" * 60,
            f" NumPy:    {self.numeric.numpy_version:<46} ",
            f" SciPy:    {self.numeric.scipy_version:<46} ",
            f" BLAS:     {self.numeric.blas_library[:46]:<46} ",
            "-" * 60,
        ])
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """Serialize to dictionary for JSON export."""
        return {
            "hostname": self.hostname,
            "os": {"name": self.os_name, "version": self.os_version},
            "python_version": self.python_version,
            "cpu": {
                "model": self.cpu.model,
                "architecture": self.cpu.architecture,
                "cores_physical": self.cpu.cores_physical,
                "cores_logical": self.cpu.cores_logical,
                "frequency_mhz": self.cpu.frequency_mhz,
                "simd_extensions": self.cpu.simd_extensions,
            },
            "memory": {
                "total_gb": self.memory.total_gb,
                "available_gb": self.memory.available_gb,
            },
            "gpu": {
                "available": self.gpu.available,
                "name": self.gpu.name,
                "vram_gb": self.gpu.vram_gb,
                "cuda_version": self.gpu.cuda_version,
            },
            "numeric_backend": {
                "numpy": self.numeric.numpy_version,
                "scipy": self.numeric.scipy_version,
                "blas": self.numeric.blas_library,
            },
            "timestamp": self.timestamp,
        }


class SystemProfiler:
    """Detects and reports system hardware/software capabilities."""

    @staticmethod
    def profile() -> SystemProfile:
        """Run full system profiling."""
        prof = SystemProfile()
        prof.hostname = platform.node()
        prof.os_name = platform.system()
        prof.os_version = platform.version()
        prof.python_version = sys.version.split()[0]
        prof.timestamp = __import__("datetime").datetime.utcnow().isoformat()

        # CPU
        prof.cpu = SystemProfiler._detect_cpu()
        # Memory
        prof.memory = SystemProfiler._detect_memory()
        # GPU
        prof.gpu = SystemProfiler._detect_gpu()
        # Numeric backends
        prof.numeric = SystemProfiler._detect_numeric()

        return prof

    @staticmethod
    def _detect_cpu() -> CPUInfo:
        info = CPUInfo()
        info.architecture = platform.machine()
        info.model = platform.processor() or "Unknown"

        try:
            import psutil
            info.cores_physical = psutil.cpu_count(logical=False) or 0
            info.cores_logical = psutil.cpu_count(logical=True) or 0
            freq = psutil.cpu_freq()
            if freq:
                info.frequency_mhz = freq.current or freq.max or 0.0
        except ImportError:
            info.cores_logical = os.cpu_count() or 0
            info.cores_physical = info.cores_logical

        # Try to get detailed CPU model on Windows
        if platform.system() == "Windows" and info.model in ("Unknown", ""):
            try:
                import subprocess
                result = subprocess.run(
                    ["wmic", "cpu", "get", "name"],
                    capture_output=True, text=True, timeout=5
                )
                lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip() and l.strip() != "Name"]
                if lines:
                    info.model = lines[0]
            except Exception:
                pass

        # SIMD detection via numpy config
        try:
            import numpy as np
            config = np.__config__
            if hasattr(config, 'blas_opt_info'):
                blas_info = config.blas_opt_info
                if 'extra_compile_args' in blas_info:
                    for arg in blas_info['extra_compile_args']:
                        if 'avx' in arg.lower() or 'sse' in arg.lower():
                            info.simd_extensions.append(arg)
        except Exception:
            pass

        # Platform-based SIMD heuristic
        if not info.simd_extensions:
            arch = info.architecture.lower()
            if 'x86_64' in arch or 'amd64' in arch:
                info.simd_extensions = ["SSE2", "AVX (assumed)"]
            elif 'aarch64' in arch or 'arm64' in arch:
                info.simd_extensions = ["NEON"]

        return info

    @staticmethod
    def _detect_memory() -> MemoryInfo:
        mem = MemoryInfo()
        try:
            import psutil
            vm = psutil.virtual_memory()
            mem.total_gb = vm.total / (1024 ** 3)
            mem.available_gb = vm.available / (1024 ** 3)
            swap = psutil.swap_memory()
            mem.swap_total_gb = swap.total / (1024 ** 3)
        except ImportError:
            log.debug("psutil not available for memory detection")
        return mem
    @staticmethod
    def _detect_gpu() -> GPUInfo:
        gpu = GPUInfo()

        # Try CuPy first
        try:
            import cupy as cp
            gpu.available = True
            gpu.cupy_available = True
            device = cp.cuda.Device(0)
            gpu.name = device.attributes.get("DeviceName", cp.cuda.runtime.getDeviceProperties(0)["name"].decode())
            mem_info = device.mem_info
            gpu.vram_gb = mem_info[1] / (1024 ** 3)
            gpu.cuda_version = f"{cp.cuda.runtime.runtimeGetVersion()}"
        except Exception:
            pass

        # Try pycuda as fallback
        if not gpu.available:
            try:
                import pycuda.driver as cuda
                import pycuda.autoinit  # noqa: F401
                device = cuda.Device(0)
                gpu.available = True
                gpu.name = device.name()
                gpu.vram_gb = device.total_memory() / (1024 ** 3)
            except Exception:
                pass

        return gpu

    @staticmethod
    def _detect_numeric() -> NumericBackend:
        nb = NumericBackend()
        try:
            import numpy as np
            nb.numpy_version = np.__version__

            # Detect BLAS backend
            try:
                config_info = np.show_config(mode='dicts')
                if isinstance(config_info, dict):
                    blas = config_info.get('Build Dependencies', {}).get('blas', {})
                    if isinstance(blas, dict):
                        nb.blas_library = blas.get('name', 'Unknown')
                    lapack = config_info.get('Build Dependencies', {}).get('lapack', {})
                    if isinstance(lapack, dict):
                        nb.lapack_library = lapack.get('name', 'Unknown')
            except Exception:
                # Older numpy fallback
                try:
                    blas_info = np.__config__.blas_opt_info
                    libs = blas_info.get('libraries', [])
                    if libs:
                        if any('mkl' in l.lower() for l in libs):
                            nb.blas_library = "Intel MKL"
                        elif any('openblas' in l.lower() for l in libs):
                            nb.blas_library = "OpenBLAS"
                        else:
                            nb.blas_library = ", ".join(libs[:3])
                except Exception:
                    pass
        except ImportError:
            pass

        try:
            import scipy
            nb.scipy_version = scipy.__version__
        except ImportError:
            nb.scipy_version = "Not installed"

        return nb
