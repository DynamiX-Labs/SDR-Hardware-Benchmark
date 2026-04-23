"""
DSP Performance Benchmarks
Measures NumPy/SciPy DSP operation throughput on the host platform.
DynamiX Labs
"""

import numpy as np
import time
from dataclasses import dataclass, field
from typing import Dict, List
import logging

log = logging.getLogger("benchmark.dsp")


@dataclass
class BenchmarkResult:
    """Single benchmark measurement."""
    name: str
    mean_ms: float
    min_ms: float
    max_ms: float
    std_ms: float
    throughput: float       # operations per second
    iterations: int
    notes: str = ""


@dataclass
class DSPBenchmarkReport:
    """Full DSP benchmark report."""
    platform: str
    python_version: str
    numpy_version: str
    results: List[BenchmarkResult] = field(default_factory=list)
    timestamp: str = ""

    def summary(self) -> str:
        lines = [
            f"DSP Benchmark Report — {self.platform}",
            f"NumPy {self.numpy_version} | Python {self.python_version}",
            "─" * 60,
            f"{'Test':<22} {'Mean':>8} {'Min':>8} {'Throughput':>14}",
            "─" * 60,
        ]
        for r in self.results:
            lines.append(
                f"{r.name:<22} {r.mean_ms:>7.2f}ms {r.min_ms:>7.2f}ms "
                f"{r.throughput:>12.0f}/s"
            )
        return "\n".join(lines)


class DSPBenchmark:
    """
    Comprehensive DSP operation benchmarks.

    Tests:
        - FFT (various sizes)
        - FIR filtering (various tap counts)
        - Decimation
        - FM demodulation
        - Resampling
        - Complex multiply-accumulate (MAC)
    """

    def __init__(self, n_samples: int = 65536, iterations: int = 500):
        self.n = n_samples
        self.iterations = iterations
        self.samples = (np.random.randn(n_samples) +
                        1j * np.random.randn(n_samples)).astype(np.complex64)
        self.real_samples = np.random.randn(n_samples).astype(np.float32)

    def _time_fn(self, fn, warmup: int = 5) -> BenchmarkResult:
        """Time a function over multiple iterations."""
        # Warmup
        for _ in range(warmup):
            fn()

        times = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            fn()
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)  # ms

        times_arr = np.array(times)
        return times_arr

    def bench_fft(self, size: int = None) -> BenchmarkResult:
        """Benchmark FFT."""
        n = size or self.n
        data = self.samples[:n].copy()
        times = self._time_fn(lambda: np.fft.fft(data))
        name = f"FFT {n//1024}k-pt"
        mean = float(np.mean(times))
        return BenchmarkResult(
            name=name,
            mean_ms=mean,
            min_ms=float(np.min(times)),
            max_ms=float(np.max(times)),
            std_ms=float(np.std(times)),
            throughput=1000 / mean,
            iterations=self.iterations,
        )

    def bench_fir_filter(self, n_taps: int = 127) -> BenchmarkResult:
        """Benchmark FIR filtering."""
        from scipy.signal import firwin, lfilter
        taps = firwin(n_taps, 0.4)
        data = self.real_samples.copy()
        times = self._time_fn(lambda: lfilter(taps, 1.0, data))
        mean = float(np.mean(times))
        return BenchmarkResult(
            name=f"FIR {n_taps}-tap",
            mean_ms=mean,
            min_ms=float(np.min(times)),
            max_ms=float(np.max(times)),
            std_ms=float(np.std(times)),
            throughput=1000 / mean,
            iterations=self.iterations,
        )

    def bench_decimate(self, factor: int = 8) -> BenchmarkResult:
        """Benchmark decimation."""
        data = self.samples.copy()
        times = self._time_fn(lambda: data[::factor])
        mean = float(np.mean(times))
        return BenchmarkResult(
            name=f"Decimate {factor}×",
            mean_ms=mean,
            min_ms=float(np.min(times)),
            max_ms=float(np.max(times)),
            std_ms=float(np.std(times)),
            throughput=1000 / mean,
            iterations=self.iterations,
        )

    def bench_fm_demod(self) -> BenchmarkResult:
        """Benchmark FM demodulation (angle differentiation)."""
        data = self.samples.copy()

        def fm_demod():
            phase = np.angle(data)
            return np.diff(np.unwrap(phase)) / np.pi

        times = self._time_fn(fm_demod)
        mean = float(np.mean(times))
        return BenchmarkResult(
            name="FM Demod",
            mean_ms=mean,
            min_ms=float(np.min(times)),
            max_ms=float(np.max(times)),
            std_ms=float(np.std(times)),
            throughput=1000 / mean,
            iterations=self.iterations,
        )

    def bench_agc(self) -> BenchmarkResult:
        """Benchmark vectorized AGC."""
        data = self.samples.copy()

        def agc():
            mag = np.abs(data)
            gain = np.where(mag > 0, 1.0 / (mag + 1e-10), 1.0)
            return data * np.clip(gain, 0.01, 100.0)

        times = self._time_fn(agc)
        mean = float(np.mean(times))
        return BenchmarkResult(
            name="AGC (vectorized)",
            mean_ms=mean,
            min_ms=float(np.min(times)),
            max_ms=float(np.max(times)),
            std_ms=float(np.std(times)),
            throughput=1000 / mean,
            iterations=self.iterations,
        )

    def run_all(self) -> DSPBenchmarkReport:
        """Run the complete DSP benchmark suite."""
        import sys
        import platform

        report = DSPBenchmarkReport(
            platform=platform.node(),
            python_version=sys.version.split()[0],
            numpy_version=np.__version__,
            timestamp=__import__("datetime").datetime.utcnow().isoformat(),
        )

        tests = [
            ("FFT 64k", lambda: self.bench_fft(65536)),
            ("FFT 8k", lambda: self.bench_fft(8192)),
            ("FIR 127-tap", lambda: self.bench_fir_filter(127)),
            ("FIR 63-tap", lambda: self.bench_fir_filter(63)),
            ("Decimate 8x", lambda: self.bench_decimate(8)),
            ("Decimate 4x", lambda: self.bench_decimate(4)),
            ("FM Demod", lambda: self.bench_fm_demod()),
            ("AGC", lambda: self.bench_agc()),
        ]

        for name, fn in tests:
            log.info(f"Running: {name}")
            result = fn()
            report.results.append(result)

        return report
