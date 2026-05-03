"""
Spectral Intelligence Engine
Automated signal detection, noise floor estimation, and modulation classification.
"""
import numpy as np
from scipy import signal as sig
import logging
from typing import List, Dict, Optional

log = logging.getLogger("satsdr.spectral")


class SpectralEngine:
    """
    Continuously analyzes the RF spectrum to detect, isolate,
    and classify signals of interest without manual frequency input.
    """

    def __init__(self, sample_rate: float, fft_size: int = 4096, noise_alpha: float = 0.05):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.noise_alpha = noise_alpha
        self.noise_floor = None  # Adaptive noise floor (dB)

    def estimate_noise_floor(self, iq_samples: np.ndarray) -> np.ndarray:
        """
        Estimate the spectral noise floor using Welch's method with
        an exponential moving average for adaptive tracking.
        """
        freqs, psd = sig.welch(
            iq_samples, fs=self.sample_rate, nperseg=self.fft_size,
            return_onesided=False, scaling='density'
        )
        psd_db = 10 * np.log10(np.fft.fftshift(psd) + 1e-20)

        if self.noise_floor is None:
            self.noise_floor = psd_db
        else:
            self.noise_floor = (1 - self.noise_alpha) * self.noise_floor + self.noise_alpha * psd_db

        return self.noise_floor

    def detect_signals(self, iq_samples: np.ndarray, threshold_db: float = 10.0) -> List[Dict]:
        """
        Detect energy peaks above the adaptive noise floor.
        Returns a list of detected signal descriptors.
        """
        noise = self.estimate_noise_floor(iq_samples)

        # Compute instantaneous PSD
        freqs, psd = sig.welch(
            iq_samples, fs=self.sample_rate, nperseg=self.fft_size,
            return_onesided=False, scaling='density'
        )
        psd_db = 10 * np.log10(np.fft.fftshift(psd) + 1e-20)
        freqs = np.fft.fftshift(freqs)

        # Find peaks above threshold
        excess = psd_db - noise
        peak_indices, properties = sig.find_peaks(excess, height=threshold_db, distance=self.fft_size // 32)

        detections = []
        for idx in peak_indices:
            center_freq_offset = freqs[idx]
            snr = excess[idx]

            # Estimate bandwidth (3dB width around peak)
            bw = self._estimate_bandwidth(excess, idx)

            detections.append({
                "center_offset_hz": float(center_freq_offset),
                "snr_db": float(snr),
                "bandwidth_hz": float(bw),
                "modulation_guess": self._classify_modulation(iq_samples, center_freq_offset, bw),
            })

        if detections:
            log.info(f"Detected {len(detections)} signal(s) of interest.")
        return detections

    def _estimate_bandwidth(self, excess_db: np.ndarray, peak_idx: int) -> float:
        """Estimate the 3dB bandwidth around a spectral peak."""
        peak_val = excess_db[peak_idx]
        threshold = peak_val - 3.0

        left = peak_idx
        while left > 0 and excess_db[left] > threshold:
            left -= 1

        right = peak_idx
        while right < len(excess_db) - 1 and excess_db[right] > threshold:
            right += 1

        freq_resolution = self.sample_rate / self.fft_size
        return (right - left) * freq_resolution

    def _classify_modulation(self, iq_samples: np.ndarray, offset_hz: float, bw_hz: float) -> dict:
        """
        Basic modulation classification using instantaneous statistics.
        Returns a dict with modulation type and confidence.
        """
        # Isolate the signal of interest via bandpass
        n = len(iq_samples)
        t = np.arange(n) / self.sample_rate
        shifted = iq_samples * np.exp(-2j * np.pi * offset_hz * t)

        # Simple variance-based heuristic
        inst_amp = np.abs(shifted)
        inst_phase = np.angle(shifted)

        amp_var = np.var(inst_amp) / (np.mean(inst_amp) ** 2 + 1e-12)
        phase_diff_var = np.var(np.diff(inst_phase))

        # Classification heuristic
        if amp_var < 0.05 and phase_diff_var > 0.5:
            return {"type": "FM", "confidence": 0.80}
        elif amp_var < 0.1:
            if phase_diff_var < 0.3:
                return {"type": "BPSK", "confidence": 0.75}
            else:
                return {"type": "QPSK", "confidence": 0.70}
        else:
            return {"type": "AM/ASK", "confidence": 0.60}
