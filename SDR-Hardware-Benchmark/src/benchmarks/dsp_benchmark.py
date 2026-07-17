"""DSP performance benchmarks for host platforms.

Extended benchmark suite inspired by:
  - SatDump: FFT DDC, Costas loop, Gardner TED, AGC, FIR RRC, M&M recovery,
             freq shift, polyphase resampler benchmarks
  - gr-satellites: CCSDS sync-word correlation, Reed-Solomon, Viterbi throughput
  - FoxTelem: Circular buffer DSP, CRC32 throughput

DynamiX Labs
"""

import numpy as np
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

log = logging.getLogger("benchmark.dsp")

# ── Benchmark Category Registry ─────────────────────────────────────────

BENCHMARK_CATEGORIES = [
    "fft",
    "fir",
    "decimate",
    "fm_demod",
    "agc",
    "costas_loop",
    "gardner_ted",
    "mm_recovery",
    "freq_shift",
    "resampler",
    "channelizer",
    "sync_word",
    "reed_solomon",
    "viterbi",
    "crc32",
]


def get_categories() -> List[str]:
    """Return all available benchmark categories."""
    return list(BENCHMARK_CATEGORIES)


# ── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    name: str
    mean_ms: float
    min_ms: float
    max_ms: float
    std_ms: float
    throughput: float       # Ops/sec
    iterations: int
    notes: str = ""
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    category: str = ""


@dataclass
class DSPBenchmarkReport:
    platform: str
    python_version: str
    numpy_version: str
    results: List[BenchmarkResult] = field(default_factory=list)
    timestamp: str = ""

    def summary(self) -> str:
        lines = [
            f"DSP Benchmark Report — {self.platform}",
            f"NumPy {self.numpy_version} | Python {self.python_version}",
            "─" * 78,
            f"{'Test':<28} {'Mean':>8} {'P50':>8} {'P95':>8} {'Throughput':>14}",
            "─" * 78,
        ]
        current_cat = ""
        for r in self.results:
            if r.category != current_cat:
                current_cat = r.category
                if len(lines) > 5:
                    lines.append("─" * 78)
            lines.append(
                f"{r.name:<28} {r.mean_ms:>7.2f}ms {r.p50_ms:>7.2f}ms "
                f"{r.p95_ms:>7.2f}ms {r.throughput:>12.0f}/s"
            )
        lines.append("─" * 78)
        return "\n".join(lines)


# ── Main Benchmark Engine ────────────────────────────────────────────────

class DSPBenchmark:
    """Comprehensive DSP operation benchmark suite."""

    def __init__(self, n_samples: int = 65536, iterations: int = 500):
        self.n = n_samples
        self.iterations = iterations
        # Pre-generate test data
        self.samples = (np.random.randn(n_samples) +
                        1j * np.random.randn(n_samples)).astype(np.complex64)
        self.real_samples = np.random.randn(n_samples).astype(np.float32)
        # Byte stream for FEC benchmarks
        self.byte_stream = np.random.bytes(n_samples // 8)

    def _time_fn(self, fn, warmup: int = 5) -> np.ndarray:
        """Time a function over multiple iterations, return array of times in ms."""
        for _ in range(warmup):
            fn()

        times = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            fn()
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)

        return np.array(times)

    def _make_result(self, name: str, times: np.ndarray,
                     category: str = "", notes: str = "") -> BenchmarkResult:
        """Build BenchmarkResult with percentile stats from raw timing data."""
        mean = float(np.mean(times))
        return BenchmarkResult(
            name=name,
            mean_ms=mean,
            min_ms=float(np.min(times)),
            max_ms=float(np.max(times)),
            std_ms=float(np.std(times)),
            throughput=1000 / mean if mean > 0 else 0,
            iterations=self.iterations,
            p50_ms=float(np.percentile(times, 50)),
            p95_ms=float(np.percentile(times, 95)),
            p99_ms=float(np.percentile(times, 99)),
            category=category,
            notes=notes,
        )

    # ── Category: FFT ────────────────────────────────────────────────────

    def bench_fft(self, size: int = None) -> BenchmarkResult:
        """Benchmark Fast Fourier Transform."""
        n = size or self.n
        name = f"FFT {n // 1024}k-pt"
        data = self.samples[:n].copy()
        times = self._time_fn(lambda: np.fft.fft(data))
        return self._make_result(name, times, category="fft")

    # ── Category: FIR Filter ─────────────────────────────────────────────

    def bench_fir_filter(self, n_taps: int = 127) -> BenchmarkResult:
        """Benchmark FIR filtering."""
        from scipy.signal import firwin, lfilter
        taps = firwin(n_taps, 0.4)
        data = self.real_samples.copy()
        times = self._time_fn(lambda: lfilter(taps, 1.0, data))
        return self._make_result(f"FIR {n_taps}-tap", times, category="fir")

    def bench_rrc_filter(self, n_taps: int = 31, sps: float = 2.0,
                         alpha: float = 0.35) -> BenchmarkResult:
        """Benchmark Root Raised Cosine filter (SatDump rrc category)."""
        from scipy.signal import firwin, lfilter

        # Generate RRC taps via windowed-sinc approximation
        t = np.arange(-n_taps // 2, n_taps // 2 + 1) / sps
        with np.errstate(divide='ignore', invalid='ignore'):
            num = np.sin(np.pi * t * (1 - alpha)) + 4 * alpha * t * np.cos(np.pi * t * (1 + alpha))
            den = np.pi * t * (1 - (4 * alpha * t) ** 2)
            taps = np.where(np.abs(den) < 1e-12, 1.0, num / den)
        taps = (taps / np.sum(taps)).astype(np.float32)

        data = self.samples.copy()
        times = self._time_fn(lambda: np.convolve(data, taps, mode='same'))
        return self._make_result(f"RRC {n_taps}-tap sps={sps}", times, category="fir",
                                 notes=f"alpha={alpha}")

    # ── Category: Decimation ─────────────────────────────────────────────

    def bench_decimate(self, factor: int = 8) -> BenchmarkResult:
        """Benchmark decimation."""
        from scipy.signal import decimate
        times = self._time_fn(lambda: decimate(self.real_samples, factor, ftype='fir'))
        return self._make_result(f"Decimate {factor}×", times, category="decimate")

    # ── Category: FM Demodulation ────────────────────────────────────────

    def bench_fm_demod(self) -> BenchmarkResult:
        """Benchmark FM demodulation."""
        data = self.samples.copy()

        def fm_demod():
            phase = np.angle(data)
            return np.diff(np.unwrap(phase)) / np.pi

        times = self._time_fn(fm_demod)
        return self._make_result("FM Demod", times, category="fm_demod")

    # ── Category: AGC ────────────────────────────────────────────────────

    def bench_agc(self) -> BenchmarkResult:
        """Benchmark Automatic Gain Control (scalar loop)."""
        data = self.samples.copy()

        def agc():
            target = 1.0
            gain = 1.0
            out = np.zeros_like(data)
            for i, s in enumerate(data):
                out[i] = s * gain
                gain *= target / (abs(s * gain) + 1e-10)
            return out

        times = self._time_fn(agc)
        return self._make_result("AGC (loop)", times, category="agc")

    def bench_agc_vectorized(self) -> BenchmarkResult:
        """Benchmark vectorized AGC (block-wise normalization)."""
        data = self.samples.copy()
        block_size = 256

        def agc_vec():
            n_blocks = len(data) // block_size
            out = data[:n_blocks * block_size].reshape(n_blocks, block_size).copy()
            for i in range(n_blocks):
                power = np.mean(np.abs(out[i]) ** 2)
                if power > 1e-12:
                    out[i] /= np.sqrt(power)
            return out.ravel()

        times = self._time_fn(agc_vec)
        return self._make_result("AGC (vectorized)", times, category="agc")

    # ── Category: Costas Loop (SatDump-inspired) ─────────────────────────

    def bench_costas_loop(self, order: int = 2) -> BenchmarkResult:
        """Benchmark Costas Loop carrier recovery (SatDump costas category)."""
        data = self.samples[:min(self.n, 8192)].copy()

        def costas():
            bw = 0.02
            denom = 1 + 2 * 0.707 * bw + bw ** 2
            alpha = 4 * 0.707 * bw / denom
            beta = 4 * bw ** 2 / denom
            phase = 0.0
            freq = 0.0
            out = np.zeros(len(data), dtype=np.complex64)

            for i in range(len(data)):
                out[i] = data[i] * np.exp(-1j * phase)
                if order == 2:
                    error = np.real(out[i]) * np.imag(out[i])
                elif order == 4:
                    re, im = np.real(out[i]), np.imag(out[i])
                    error = np.sign(re) * im - np.sign(im) * re
                elif order == 8:
                    ang = np.angle(out[i])
                    error = np.sin(8 * ang) / 8
                else:
                    error = np.real(out[i]) * np.imag(out[i])
                freq += beta * error
                phase += freq + alpha * error

            return out

        times = self._time_fn(costas)
        return self._make_result(f"Costas Order-{order}", times, category="costas_loop")

    # ── Category: Gardner TED (SatDump-inspired) ─────────────────────────

    def bench_gardner_ted(self, sps: float = 4.0) -> BenchmarkResult:
        """Benchmark Gardner Timing Error Detector (SatDump simple_gardner)."""
        data = self.samples[:min(self.n, 8192)].copy()

        def gardner():
            symbols = []
            mu = 0.0
            gain = 0.05
            idx = sps

            while idx < len(data) - sps:
                i = int(idx)
                sym = data[i]
                symbols.append(sym)
                mid_idx = int(idx - sps / 2)
                prev_idx = int(idx - sps)
                if prev_idx >= 0 and mid_idx >= 0:
                    error = np.real(
                        (data[i] - data[prev_idx]) * np.conj(data[mid_idx])
                    )
                    mu = gain * error
                idx += sps + mu
            return np.array(symbols, dtype=np.complex64)

        times = self._time_fn(gardner)
        return self._make_result(f"Gardner TED sps={sps:.1f}", times, category="gardner_ted")

    # ── Category: M&M Clock Recovery (SatDump-inspired) ──────────────────

    def bench_mm_recovery(self, omega: float = 2.0) -> BenchmarkResult:
        """Benchmark Mueller & Müller clock recovery (SatDump mm_recovery)."""
        data = self.samples[:min(self.n, 8192)].copy()

        def mm_recover():
            mu = 0.0
            omega_val = omega
            gain_omega = 0.01
            gain_mu = 0.01
            omega_mid = omega
            omega_lim = 0.1
            symbols = []
            idx = 0
            last_sample = data[0]
            last_symbol = data[0]

            while idx < len(data) - 2:
                i = int(idx)
                sample = data[i]
                symbols.append(sample)

                # M&M TED
                error = np.real(
                    (sample - last_symbol) * np.conj(last_sample) -
                    (last_sample - sample) * np.conj(last_symbol)
                ) / 2.0

                last_symbol = sample
                mid = int(idx + omega_val / 2)
                if mid < len(data):
                    last_sample = data[mid]

                omega_val = omega_val + gain_omega * error
                omega_val = np.clip(omega_val, omega_mid - omega_lim,
                                    omega_mid + omega_lim)
                mu += omega_val + gain_mu * error
                idx += int(omega_val)

            return np.array(symbols, dtype=np.complex64)

        times = self._time_fn(mm_recover)
        return self._make_result(f"M&M Recovery ω={omega:.1f}", times,
                                 category="mm_recovery")

    # ── Category: Frequency Shift (SatDump-inspired) ─────────────────────

    def bench_freq_shift(self, shift_hz: float = 100e3,
                         sample_rate: float = 1e6) -> BenchmarkResult:
        """Benchmark frequency shifting (complex NCO mixer)."""
        data = self.samples.copy()
        t = np.arange(len(data)) / sample_rate
        nco = np.exp(2j * np.pi * shift_hz * t).astype(np.complex64)

        def freq_shift():
            return data * nco

        times = self._time_fn(freq_shift)
        return self._make_result("Freq Shift 100kHz", times, category="freq_shift")

    # ── Category: Resampler (SatDump-inspired) ───────────────────────────

    def bench_resampler(self, up: int = 1, down: int = 10) -> BenchmarkResult:
        """Benchmark rational resampling (SatDump resamplers)."""
        from scipy.signal import resample_poly
        data = self.samples.copy()

        def resamp():
            return resample_poly(data, up, down)

        times = self._time_fn(resamp)
        ratio = f"{up}/{down}" if up != 1 else f"1/{down}"
        return self._make_result(f"Resample {ratio}", times, category="resampler")

    # ── Category: Channelizer ────────────────────────────────────────────

    def bench_channelizer(self, num_channels: int = 8) -> BenchmarkResult:
        """Benchmark FFT channelizer."""
        block_len = self.n // num_channels
        data = self.samples[:block_len * num_channels].reshape(num_channels, block_len)

        def channelize():
            return np.fft.fft(data, axis=1)

        times = self._time_fn(channelize)
        return self._make_result(f"Channelizer {num_channels}ch", times,
                                 category="channelizer")

    # ── Category: Sync Word Detection (gr-satellites inspired) ───────────

    def bench_sync_word_detection(self) -> BenchmarkResult:
        """Benchmark CCSDS sync word correlation (gr-satellites pattern)."""
        sync = np.array([1, -1, 1, 1, -1, 1, -1, -1,
                         1, 1, 0, 0, 1, 1, 1, 1,
                         1, 1, 1, 1, 1, 1, 0, 0,
                         0, 0, 0, 1, 1, 1, 0, 1], dtype=np.float32) * 2 - 1
        stream = np.random.randn(self.n).astype(np.float32)

        def correlate():
            return np.correlate(stream, sync, mode='valid')

        times = self._time_fn(correlate)
        return self._make_result("Sync Word Corr", times, category="sync_word")

    # ── Category: Reed-Solomon (gr-satellites inspired) ───────────────────

    def bench_reed_solomon(self) -> BenchmarkResult:
        """Benchmark Reed-Solomon RS(255,223) encoding/decoding throughput."""
        # Simulate RS encode: GF(2^8) polynomial evaluation
        block = np.random.randint(0, 256, size=223, dtype=np.uint8)
        # Simplified GF multiplication table
        gf_exp = np.zeros(512, dtype=np.uint8)
        gf_log = np.zeros(256, dtype=np.uint8)
        x = 1
        for i in range(255):
            gf_exp[i] = x
            gf_log[x] = i
            x = ((x << 1) ^ 0x11d) if x & 0x80 else (x << 1)
            x &= 0xFF
        gf_exp[255:510] = gf_exp[:255]

        def rs_encode():
            # Generate 32 parity symbols using generator polynomial evaluation
            parity = np.zeros(32, dtype=np.uint8)
            for byte in block:
                feedback = byte ^ parity[0]
                parity = np.roll(parity, -1)
                parity[-1] = 0
                if feedback != 0:
                    log_fb = gf_log[feedback]
                    for j in range(32):
                        parity[j] ^= gf_exp[(log_fb + j) % 255]
            return np.concatenate([block, parity])

        times = self._time_fn(rs_encode)
        return self._make_result("RS(255,223) Encode", times, category="reed_solomon")

    # ── Category: Viterbi (gr-satellites inspired) ────────────────────────

    def bench_viterbi(self) -> BenchmarkResult:
        """Benchmark Viterbi-style trellis decoding throughput."""
        n_bits = min(self.n, 4096)
        soft_symbols = np.random.randn(n_bits * 2).astype(np.float32)

        def viterbi_hard():
            # Hard-decision path metric computation (simplified K=7, R=1/2)
            n_states = 64  # 2^(K-1)
            metrics = np.full(n_states, -np.inf, dtype=np.float32)
            metrics[0] = 0.0
            new_metrics = np.full(n_states, -np.inf, dtype=np.float32)

            for i in range(0, len(soft_symbols) - 1, 2):
                s0, s1 = soft_symbols[i], soft_symbols[i + 1]
                new_metrics.fill(-np.inf)
                for state in range(n_states):
                    if metrics[state] == -np.inf:
                        continue
                    for bit in (0, 1):
                        # Simplified transition
                        next_state = ((state << 1) | bit) & (n_states - 1)
                        branch = s0 * (1 if bit else -1) + s1 * (1 if (state & 1) else -1)
                        candidate = metrics[state] + branch
                        if candidate > new_metrics[next_state]:
                            new_metrics[next_state] = candidate
                metrics[:] = new_metrics

            return int(np.argmax(metrics))

        times = self._time_fn(viterbi_hard)
        return self._make_result(f"Viterbi K=7 R=1/2", times, category="viterbi",
                                 notes=f"{n_bits} bits")

    # ── Category: CRC32 (FoxTelem inspired) ──────────────────────────────

    def bench_crc32(self) -> BenchmarkResult:
        """Benchmark CRC32 computation (FoxTelem Crc32 pattern)."""
        import zlib
        data = self.byte_stream

        def crc_compute():
            return zlib.crc32(data)

        times = self._time_fn(crc_compute)
        return self._make_result(f"CRC32 {len(data)}B", times, category="crc32")

    # ── Run All ──────────────────────────────────────────────────────────

    def run_all(self, categories: List[str] = None) -> DSPBenchmarkReport:
        """Run the complete DSP benchmark suite, optionally filtered by categories."""
        import sys
        import platform

        report = DSPBenchmarkReport(
            platform=platform.node(),
            python_version=sys.version.split()[0],
            numpy_version=np.__version__,
            timestamp=__import__("datetime").datetime.utcnow().isoformat(),
        )

        all_tests = [
            # FFT
            ("fft", "FFT 64k", lambda: self.bench_fft(65536)),
            ("fft", "FFT 8k", lambda: self.bench_fft(8192)),
            ("fft", "FFT 1k", lambda: self.bench_fft(1024)),
            # FIR
            ("fir", "FIR 127-tap", lambda: self.bench_fir_filter(127)),
            ("fir", "FIR 63-tap", lambda: self.bench_fir_filter(63)),
            ("fir", "RRC 31-tap", lambda: self.bench_rrc_filter(31)),
            ("fir", "RRC 361-tap", lambda: self.bench_rrc_filter(361)),
            # Decimation
            ("decimate", "Decimate 8x", lambda: self.bench_decimate(8)),
            ("decimate", "Decimate 4x", lambda: self.bench_decimate(4)),
            # FM Demod
            ("fm_demod", "FM Demod", lambda: self.bench_fm_demod()),
            # AGC
            ("agc", "AGC Loop", lambda: self.bench_agc()),
            ("agc", "AGC Vectorized", lambda: self.bench_agc_vectorized()),
            # Costas Loop
            ("costas_loop", "Costas-2", lambda: self.bench_costas_loop(2)),
            ("costas_loop", "Costas-4", lambda: self.bench_costas_loop(4)),
            ("costas_loop", "Costas-8", lambda: self.bench_costas_loop(8)),
            # Gardner TED
            ("gardner_ted", "Gardner 2.0", lambda: self.bench_gardner_ted(2.0)),
            ("gardner_ted", "Gardner 4.0", lambda: self.bench_gardner_ted(4.0)),
            # M&M Recovery
            ("mm_recovery", "M&M 1.2", lambda: self.bench_mm_recovery(1.2)),
            ("mm_recovery", "M&M 2.0", lambda: self.bench_mm_recovery(2.0)),
            ("mm_recovery", "M&M 3.0", lambda: self.bench_mm_recovery(3.0)),
            # Freq Shift
            ("freq_shift", "Freq Shift", lambda: self.bench_freq_shift()),
            # Resampler
            ("resampler", "Resample 1/10", lambda: self.bench_resampler(1, 10)),
            ("resampler", "Resample 1/4", lambda: self.bench_resampler(1, 4)),
            # Channelizer
            ("channelizer", "Channelizer 8ch", lambda: self.bench_channelizer(8)),
            ("channelizer", "Channelizer 16ch", lambda: self.bench_channelizer(16)),
            # Sync Word
            ("sync_word", "Sync Corr", lambda: self.bench_sync_word_detection()),
            # Reed-Solomon
            ("reed_solomon", "RS Encode", lambda: self.bench_reed_solomon()),
            # Viterbi
            ("viterbi", "Viterbi K=7", lambda: self.bench_viterbi()),
            # CRC32
            ("crc32", "CRC32", lambda: self.bench_crc32()),
        ]

        for cat, name, fn in all_tests:
            if categories and cat not in categories:
                continue
            log.info(f"Running: {name}")
            try:
                result = fn()
                report.results.append(result)
            except Exception as e:
                log.warning(f"Benchmark '{name}' failed: {e}")

        return report


class SDRHardwareBenchmark:
    """Hardware benchmark stub for SoapySDR."""
    def __init__(self, driver="rtlsdr"):
        try:
            import SoapySDR
            self.sdr = SoapySDR.Device({"driver": driver})
        except ImportError:
            log.warning("SoapySDR not installed, hardware benchmarks will fail.")
            self.sdr = None

    def bench_throughput(self, rate=2.4e6) -> BenchmarkResult:
        raise NotImplementedError("Hardware throughput benchmark requires SoapySDR hardware")
