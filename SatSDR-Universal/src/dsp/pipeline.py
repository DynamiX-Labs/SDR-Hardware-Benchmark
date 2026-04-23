"""
DSP Pipeline Builder
Constructs signal processing chains from YAML config or programmatically.
DynamiX Labs
"""

import numpy as np
from typing import List, Callable, Optional
import logging

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

    def __init__(self, sample_rate: float = 250_000):
        self.sample_rate = sample_rate
        self.blocks: List[DSPBlock] = []
        self._current_rate = sample_rate

    def add_lowpass(self, cutoff: float, num_taps: int = 127) -> "Pipeline":
        """Add FIR low-pass filter."""
        from scipy.signal import firwin, lfilter
        taps = firwin(num_taps, cutoff / (self._current_rate / 2))
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

    def info(self) -> str:
        """Return pipeline description."""
        blocks_str = " → ".join(b.name for b in self.blocks)
        return f"Pipeline [{blocks_str}] | out_rate={self._current_rate/1e3:.1f}kSPS"
