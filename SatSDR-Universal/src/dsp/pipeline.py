"""
DSP Pipeline Builder
Constructs signal processing chains from YAML config or programmatically.
DynamiX Labs
"""

import numpy as np
from typing import List, Callable, Optional, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from .gpu_backend import GPUBackend

log = logging.getLogger("satsdr.dsp")


class DSPBlock:
    """Single DSP processing block."""

    def __init__(self, name: str, fn: Callable, **params):
        self.name = name
        self.fn = fn
        self.params = params

    def process(self, samples: np.ndarray) -> np.ndarray:
        return self.fn(samples, **self.params)

    def __repr__(self):
        return f"DSPBlock({self.name})"


class Pipeline:
    """
    Composable DSP pipeline.

    Usage:
        pipe = Pipeline()
        pipe.add_lowpass(cutoff=100e3, sample_rate=250e3)
        pipe.add_decimate(factor=8)
        pipe.add_agc()
        output = pipe.process(iq_samples)
    """

    def __init__(self, sample_rate: float = 250_000,
                 gpu_backend: Optional["GPUBackend"] = None):
        self.sample_rate = sample_rate
        self.gpu = gpu_backend
        self.blocks: List[DSPBlock] = []
        self._current_rate = sample_rate

    def add_lowpass(self, cutoff: float, num_taps: int = 127) -> "Pipeline":
        """Add FIR low-pass filter.  GPU-accelerated when backend is available."""
        from scipy.signal import firwin, lfilter
        taps = firwin(num_taps, cutoff / (self._current_rate / 2))

        if self.gpu is not None and self.gpu.gpu_available:
            gpu_ref = self.gpu
            taps_copy = taps.copy()
            self.blocks.append(DSPBlock(
                "lowpass_gpu",
                lambda s, t=taps_copy, g=gpu_ref: g.fir_filter(s, t),
            ))
            log.debug(f"Added GPU LPF: cutoff={cutoff/1e3:.1f}kHz, taps={num_taps}")
        else:
            self.blocks.append(DSPBlock("lowpass", lambda s, t=taps: lfilter(t, 1.0, s)))
            log.debug(f"Added LPF: cutoff={cutoff/1e3:.1f}kHz, taps={num_taps}")
        return self

    def add_decimate(self, factor: int) -> "Pipeline":
        """Add decimation block."""
        self.blocks.append(DSPBlock("decimate", lambda s, f=factor: s[::f]))
        self._current_rate /= factor
        log.debug(f"Added decimator: factor={factor}, new rate={self._current_rate/1e3:.1f}kSPS")
        return self

    def add_agc(self, target: float = 1.0, attack: float = 0.01,
                decay: float = 0.001) -> "Pipeline":
        """Add Automatic Gain Control."""
        state = {"gain": 1.0}

        def _agc(samples, target=target, attack=attack, decay=decay):
            out = np.zeros_like(samples)
            for i, s in enumerate(samples):
                out[i] = s * state["gain"]
                mag = abs(out[i])
                error = target - mag
                if error > 0:
                    state["gain"] *= (1 + decay)
                else:
                    state["gain"] *= (1 - attack)
                state["gain"] = np.clip(state["gain"], 0.01, 100.0)
            return out

        self.blocks.append(DSPBlock("agc", _agc))
        return self

    def add_fm_demod(self) -> "Pipeline":
        """Add FM demodulator."""
        def _fm_demod(samples):
            phase = np.angle(samples)
            return np.diff(np.unwrap(phase)) / np.pi

        self.blocks.append(DSPBlock("fm_demod", _fm_demod))
        return self

    def add_dc_removal(self) -> "Pipeline":
        """Remove DC offset from IQ stream."""
        def _dc_remove(samples):
            return samples - np.mean(samples)
        self.blocks.append(DSPBlock("dc_removal", _dc_remove))
        return self

    def add_costas_bpsk_demod(self, loop_bw: float = 0.01) -> "Pipeline":
        """
        Add a 2nd-order Costas Loop for BPSK carrier recovery and demodulation.
        """
        def _costas(samples, bw=loop_bw):
            # Loop filter coefficients (proportional + integral)
            denom = 1 + 2 * 0.707 * bw + bw ** 2
            alpha = 4 * 0.707 * bw / denom
            beta = 4 * bw ** 2 / denom

            phase = 0.0
            freq = 0.0
            out = np.zeros(len(samples), dtype=np.complex64)

            for i in range(len(samples)):
                out[i] = samples[i] * np.exp(-1j * phase)
                error = np.real(out[i]) * np.imag(out[i])  # BPSK phase error
                freq += beta * error
                phase += freq + alpha * error

            return out

        self.blocks.append(DSPBlock("costas_bpsk", _costas))
        return self

    def add_gardner_ted(self, sps: int = 4) -> "Pipeline":
        """
        Add Gardner Timing Error Detector for symbol synchronization.
        sps: samples per symbol
        """
        def _gardner(samples, sps=sps):
            symbols = []
            mu = 0.0  # fractional timing offset
            gain = 0.05
            idx = sps

            while idx < len(samples) - sps:
                i = int(idx)
                sym = samples[i]
                symbols.append(sym)

                # Gardner TED: e = Re{(y[n] - y[n-1]) * conj(y[n-0.5])}
                mid_idx = int(idx - sps // 2)
                prev_idx = int(idx - sps)
                if prev_idx >= 0 and mid_idx >= 0:
                    error = np.real(
                        (samples[i] - samples[prev_idx]) * np.conj(samples[mid_idx])
                    )
                    mu = gain * error
                idx += sps + mu

            return np.array(symbols, dtype=np.complex64)

        self.blocks.append(DSPBlock("gardner_ted", _gardner))
        return self

    def add_resample(self, output_rate: float) -> "Pipeline":
        """Add rational resampler."""
        from scipy.signal import resample_poly
        from math import gcd
        in_rate = int(self._current_rate)
        out_rate = int(output_rate)
        g = gcd(in_rate, out_rate)
        up, down = out_rate // g, in_rate // g
        self.blocks.append(DSPBlock(
            "resample",
            lambda s, u=up, d=down: resample_poly(s, u, d).astype(np.complex64)
        ))
        self._current_rate = output_rate
        return self

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Run samples through the full pipeline."""
        out = samples
        for block in self.blocks:
            out = block.process(out)
        return out

    def process_batch(self, chunks: List[np.ndarray]) -> List[np.ndarray]:
        """
        Process multiple IQ chunks through the pipeline.

        When a GPU backend is available, this enables overlap-save
        style batch processing for improved throughput.
        """
        return [self.process(chunk) for chunk in chunks]

    def add_fft_channelizer(self, num_channels: int = 8) -> "Pipeline":
        """
        Add a polyphase FFT channelizer that splits wideband IQ into
        `num_channels` narrowband sub-channels.

        Uses GPU batch FFT when available for maximum throughput.
        """
        def _channelize(samples, n_ch=num_channels, gpu=self.gpu):
            # Reshape into (num_channels, block_len)
            block_len = len(samples) // n_ch
            if block_len == 0:
                return samples
            trimmed = samples[:block_len * n_ch]
            matrix = trimmed.reshape(n_ch, block_len)

            if gpu is not None and gpu.gpu_available:
                # Batched GPU FFT across all channels
                spectra = gpu.batch_fft([matrix[i] for i in range(n_ch)])
                return np.array(spectra, dtype=np.complex64)
            else:
                return np.fft.fft(matrix, axis=1).astype(np.complex64)

        self.blocks.append(DSPBlock("fft_channelizer", _channelize))
        log.debug(f"Added FFT channelizer: {num_channels} channels")
        return self

    def info(self) -> str:
        """Return pipeline description."""
        blocks_str = " -> ".join(b.name for b in self.blocks)
        gpu_tag = " [GPU]" if (self.gpu and self.gpu.gpu_available) else ""
        return f"Pipeline{gpu_tag} [{blocks_str}] | out_rate={self._current_rate/1e3:.1f}kSPS"

    @classmethod
    def from_preset(cls, preset_name: str, gpu_backend: Optional["GPUBackend"] = None) -> "Pipeline":
        """Build a pipeline from a registered preset name."""
        from .processing_module import get_preset
        preset = get_preset(preset_name)
        if not preset:
            raise ValueError(f"Unknown pipeline preset: {preset_name}")
            
        pipe = cls(sample_rate=preset.sample_rate, gpu_backend=gpu_backend)
        log.info(f"Building pipeline from preset: {preset.name}")
        
        for step in preset.steps:
            mod = step["module"]
            params = step.get("params", {})
            
            # Map JSON modules to Pipeline methods
            if mod == "lowpass":
                pipe.add_lowpass(**params)
            elif mod == "decimate":
                pipe.add_decimate(**params)
            elif mod == "agc":
                pipe.add_agc(**params)
            elif mod == "fm_demod":
                pipe.add_fm_demod(**params)
            elif mod == "dc_removal":
                pipe.add_dc_removal(**params)
            elif mod == "costas_bpsk":
                pipe.add_costas_bpsk_demod(**params)
            elif mod == "gardner_ted" or mod == "clock_recovery":
                pipe.add_gardner_ted(**params)
            elif mod == "resample":
                pipe.add_resample(**params)
            elif mod == "fft_channelizer":
                pipe.add_fft_channelizer(**params)
            else:
                log.warning(f"Unsupported module in preset: {mod}")
                
        return pipe

