"""
GPU-Accelerated DSP Backend
CuPy/CUDA FFT offload with transparent NumPy fallback.
DynamiX Labs | Phase 3

Provides a unified compute interface that transparently dispatches to
CUDA kernels via CuPy when a compatible GPU is detected, falling back
to NumPy/SciPy on CPU-only systems. All public methods accept and
return NumPy arrays — GPU transfers are handled internally.

Usage:
    gpu = GPUBackend()
    psd_db = gpu.welch_psd(iq_samples, fs=250e3, nperseg=4096)
    filtered = gpu.fir_filter(iq_samples, taps)
    spectrum = gpu.fft(iq_samples)
"""

import numpy as np
import logging
import time
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

log = logging.getLogger("satsdr.gpu")

# ---------------------------------------------------------------------------
# Lazy CuPy import — never crashes on CPU-only machines
# ---------------------------------------------------------------------------
_cupy = None
_cupy_fft = None
_cupyx_scipy_signal = None


def _try_import_cupy():
    """Attempt to import CuPy and its submodules exactly once."""
    global _cupy, _cupy_fft, _cupyx_scipy_signal
    if _cupy is not None:
        return True
    try:
        import cupy  # type: ignore[import-untyped]
        import cupy.fft as cupy_fft  # type: ignore[import-untyped]
        import cupyx.scipy.signal as cupyx_signal  # type: ignore[import-untyped]
        _cupy = cupy
        _cupy_fft = cupy_fft
        _cupyx_scipy_signal = cupyx_signal
        return True
    except (ImportError, Exception):
        return False


# ---------------------------------------------------------------------------
# Performance telemetry
# ---------------------------------------------------------------------------
@dataclass
class GPUMetrics:
    """Accumulated performance counters for the GPU backend."""

    total_fft_calls: int = 0
    total_fir_calls: int = 0
    total_psd_calls: int = 0
    total_transfer_bytes: int = 0
    cumulative_kernel_time_s: float = 0.0
    cumulative_transfer_time_s: float = 0.0
    last_kernel_time_ms: float = 0.0
    gpu_name: str = ""
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of current metrics."""
        return {
            "fft_calls": self.total_fft_calls,
            "fir_calls": self.total_fir_calls,
            "psd_calls": self.total_psd_calls,
            "transfer_MB": round(self.total_transfer_bytes / 1e6, 2),
            "kernel_time_s": round(self.cumulative_kernel_time_s, 4),
            "transfer_time_s": round(self.cumulative_transfer_time_s, 4),
            "last_kernel_ms": round(self.last_kernel_time_ms, 3),
            "gpu": self.gpu_name,
            "vram_total_MB": round(self.vram_total_mb, 1),
            "vram_used_MB": round(self.vram_used_mb, 1),
        }


# ---------------------------------------------------------------------------
# Main backend class
# ---------------------------------------------------------------------------
class GPUBackend:
    """
    Unified compute backend — CUDA/CuPy with NumPy fallback.

    The backend automatically probes for a CUDA-capable device on
    construction.  Every public method transparently:
      1. Transfers host (NumPy) arrays to device (CuPy) memory.
      2. Executes the CUDA kernel.
      3. Transfers results back to host NumPy arrays.
    If no GPU is available, the same methods execute the equivalent
    NumPy/SciPy code path without any API change.

    Parameters
    ----------
    device_id : int
        CUDA device ordinal (default 0).
    enable_pinned : bool
        Use CUDA pinned (page-locked) memory for faster H↔D transfers.
    """

    def __init__(self, device_id: int = 0, enable_pinned: bool = True):
        self.device_id = device_id
        self.enable_pinned = enable_pinned
        self.gpu_available = False
        self.metrics = GPUMetrics()

        self._pool = None  # CuPy pinned-memory pool

        self.gpu_available = self._probe_cuda()
        if self.gpu_available:
            log.info(
                f"GPU backend active: {self.metrics.gpu_name} | "
                f"VRAM {self.metrics.vram_total_mb:.0f} MB | "
                f"device={self.device_id}"
            )
        else:
            log.info("GPU unavailable — using NumPy/SciPy CPU fallback")

    # ------------------------------------------------------------------
    # Context manager — clean up pinned-memory pool
    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

    def release(self):
        """Free GPU memory pools and synchronise device."""
        if self.gpu_available and _cupy is not None:
            try:
                _cupy.get_default_memory_pool().free_all_blocks()
                if self._pool is not None:
                    self._pool.free_all_blocks()
                _cupy.cuda.Device(self.device_id).synchronize()
                log.debug("GPU memory pools released")
            except Exception as exc:
                log.warning(f"GPU cleanup warning: {exc}")

    # ------------------------------------------------------------------
    # CUDA probe
    # ------------------------------------------------------------------
    def _probe_cuda(self) -> bool:
        """Detect CUDA/CuPy availability and populate device info."""
        if not _try_import_cupy():
            return False
        try:
            _cupy.cuda.Device(self.device_id).use()
            props = _cupy.cuda.runtime.getDeviceProperties(self.device_id)
            self.metrics.gpu_name = props["name"].decode() if isinstance(props["name"], bytes) else str(props["name"])
            self.metrics.vram_total_mb = props["totalGlobalMem"] / (1024 ** 2)

            # Set up pinned-memory pool for faster transfers
            if self.enable_pinned:
                self._pool = _cupy.cuda.PinnedMemoryPool()
                _cupy.cuda.set_pinned_memory_allocator(self._pool.malloc)

            return True
        except Exception as exc:
            log.debug(f"CUDA probe failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _to_device(self, x: np.ndarray) -> "object":
        """Host → Device transfer with timing."""
        t0 = time.perf_counter()
        d = _cupy.asarray(x)
        _cupy.cuda.Device(self.device_id).synchronize()
        dt = time.perf_counter() - t0
        self.metrics.total_transfer_bytes += x.nbytes
        self.metrics.cumulative_transfer_time_s += dt
        return d

    def _to_host(self, d) -> np.ndarray:
        """Device → Host transfer."""
        t0 = time.perf_counter()
        h = _cupy.asnumpy(d)
        dt = time.perf_counter() - t0
        self.metrics.cumulative_transfer_time_s += dt
        self.metrics.total_transfer_bytes += h.nbytes
        return h

    def _update_vram(self):
        """Refresh VRAM usage metric."""
        try:
            pool = _cupy.get_default_memory_pool()
            self.metrics.vram_used_mb = pool.used_bytes() / (1024 ** 2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # FFT
    # ------------------------------------------------------------------
    def fft(self, x: np.ndarray, n: Optional[int] = None) -> np.ndarray:
        """
        Compute 1-D FFT.  GPU-accelerated when available.

        Parameters
        ----------
        x : np.ndarray
            Input samples (real or complex).
        n : int, optional
            FFT length (zero-padded or truncated).

        Returns
        -------
        np.ndarray
            Complex spectrum (NumPy array on host).
        """
        if self.gpu_available:
            t0 = time.perf_counter()
            d_x = self._to_device(x)
            d_out = _cupy_fft.fft(d_x, n=n)
            result = self._to_host(d_out)
            dt = time.perf_counter() - t0
            self.metrics.total_fft_calls += 1
            self.metrics.cumulative_kernel_time_s += dt
            self.metrics.last_kernel_time_ms = dt * 1000
            self._update_vram()
            return result
        else:
            self.metrics.total_fft_calls += 1
            return np.fft.fft(x, n=n)

    def ifft(self, X: np.ndarray, n: Optional[int] = None) -> np.ndarray:
        """Compute 1-D IFFT.  GPU-accelerated when available."""
        if self.gpu_available:
            t0 = time.perf_counter()
            d_X = self._to_device(X)
            d_out = _cupy_fft.ifft(d_X, n=n)
            result = self._to_host(d_out)
            dt = time.perf_counter() - t0
            self.metrics.cumulative_kernel_time_s += dt
            self.metrics.last_kernel_time_ms = dt * 1000
            return result
        else:
            return np.fft.ifft(X, n=n)

    def batch_fft(self, blocks: List[np.ndarray],
                  n: Optional[int] = None) -> List[np.ndarray]:
        """
        Batch FFT — processes a list of sample blocks.

        On GPU this stacks into a 2-D array for a single batched kernel
        launch, significantly reducing overhead versus sequential calls.
        """
        if not blocks:
            return []

        if self.gpu_available:
            # Stack into (batch, N) matrix
            max_len = max(len(b) for b in blocks)
            fft_n = n or max_len
            padded = np.zeros((len(blocks), fft_n), dtype=np.complex64)
            for i, b in enumerate(blocks):
                padded[i, :len(b)] = b

            t0 = time.perf_counter()
            d_in = self._to_device(padded)
            d_out = _cupy_fft.fft(d_in, n=fft_n, axis=1)
            result = self._to_host(d_out)
            dt = time.perf_counter() - t0
            self.metrics.total_fft_calls += len(blocks)
            self.metrics.cumulative_kernel_time_s += dt
            self.metrics.last_kernel_time_ms = dt * 1000
            self._update_vram()
            return [result[i] for i in range(len(blocks))]
        else:
            self.metrics.total_fft_calls += len(blocks)
            return [np.fft.fft(b, n=n) for b in blocks]

    # ------------------------------------------------------------------
    # FIR filtering
    # ------------------------------------------------------------------
    def fir_filter(self, x: np.ndarray, taps: np.ndarray) -> np.ndarray:
        """
        Apply FIR filter via frequency-domain convolution (overlap-save).

        On GPU, the convolution is performed entirely in CUDA via
        `cupyx.scipy.signal.fftconvolve`.  On CPU, falls back to
        `scipy.signal.fftconvolve`.

        Parameters
        ----------
        x : np.ndarray
            Input signal (complex64 or float32).
        taps : np.ndarray
            FIR filter coefficients.

        Returns
        -------
        np.ndarray
            Filtered signal (same dtype as input).
        """
        if self.gpu_available:
            t0 = time.perf_counter()
            d_x = self._to_device(x)
            d_taps = self._to_device(taps)
            d_out = _cupyx_scipy_signal.fftconvolve(d_x, d_taps, mode="same")
            result = self._to_host(d_out).astype(x.dtype)
            dt = time.perf_counter() - t0
            self.metrics.total_fir_calls += 1
            self.metrics.cumulative_kernel_time_s += dt
            self.metrics.last_kernel_time_ms = dt * 1000
            self._update_vram()
            return result
        else:
            from scipy.signal import fftconvolve
            self.metrics.total_fir_calls += 1
            return fftconvolve(x, taps, mode="same").astype(x.dtype)

    # ------------------------------------------------------------------
    # Welch PSD estimation
    # ------------------------------------------------------------------
    def welch_psd(
        self,
        x: np.ndarray,
        fs: float,
        nperseg: int = 4096,
        noverlap: Optional[int] = None,
        window: str = "hann",
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Welch's method PSD estimation.

        On GPU, the periodogram segments are computed via batched FFT
        on CUDA.  On CPU, delegates to `scipy.signal.welch`.

        Returns
        -------
        freqs : np.ndarray
            Frequency axis in Hz (two-sided, FFT-shifted).
        psd_db : np.ndarray
            Power spectral density in dB.
        """
        self.metrics.total_psd_calls += 1

        if self.gpu_available:
            return self._welch_gpu(x, fs, nperseg, noverlap, window)
        else:
            return self._welch_cpu(x, fs, nperseg, noverlap, window)

    def _welch_cpu(self, x, fs, nperseg, noverlap, window):
        from scipy import signal as sig
        freqs, psd = sig.welch(
            x, fs=fs, nperseg=nperseg, noverlap=noverlap,
            window=window, return_onesided=False, scaling="density",
        )
        psd_db = 10.0 * np.log10(np.fft.fftshift(psd) + 1e-20)
        freqs = np.fft.fftshift(freqs)
        return freqs, psd_db

    def _welch_gpu(self, x, fs, nperseg, noverlap, window):
        """GPU-accelerated Welch PSD via batched CUDA FFT."""
        from scipy.signal.windows import get_window

        if noverlap is None:
            noverlap = nperseg // 2

        # Build window on host, transfer once
        win = get_window(window, nperseg).astype(np.float32)
        win_norm = np.sum(win ** 2)

        # Segment the signal
        step = nperseg - noverlap
        n_segments = max(1, (len(x) - nperseg) // step + 1)

        segments = np.zeros((n_segments, nperseg), dtype=np.complex64)
        for i in range(n_segments):
            start = i * step
            segments[i] = x[start : start + nperseg]

        t0 = time.perf_counter()
        d_segments = self._to_device(segments)
        d_win = self._to_device(win)

        # Apply window
        d_windowed = d_segments * d_win[None, :]

        # Batched FFT
        d_fft = _cupy_fft.fft(d_windowed, n=nperseg, axis=1)

        # Periodogram — |X|² / (fs * Σw²)
        d_psd = _cupy.abs(d_fft) ** 2 / (fs * win_norm)

        # Average across segments
        d_avg = _cupy.mean(d_psd, axis=0)

        psd = self._to_host(d_avg)
        dt = time.perf_counter() - t0
        self.metrics.cumulative_kernel_time_s += dt
        self.metrics.last_kernel_time_ms = dt * 1000
        self._update_vram()

        psd_db = 10.0 * np.log10(np.fft.fftshift(psd) + 1e-20)
        freqs = np.fft.fftshift(np.fft.fftfreq(nperseg, d=1.0 / fs))
        return freqs, psd_db

    # ------------------------------------------------------------------
    # Correlation
    # ------------------------------------------------------------------
    def correlate(self, a: np.ndarray, b: np.ndarray,
                  mode: str = "full") -> np.ndarray:
        """Cross-correlation — GPU-accelerated when available."""
        if self.gpu_available:
            d_a = self._to_device(a)
            d_b = self._to_device(b)
            d_out = _cupyx_scipy_signal.fftconvolve(
                d_a, d_b[::-1].conj(), mode=mode
            )
            return self._to_host(d_out)
        else:
            from scipy.signal import fftconvolve
            return fftconvolve(a, b[::-1].conj(), mode=mode)
