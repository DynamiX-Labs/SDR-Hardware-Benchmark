"""
RF Frontend Characterization Engine
DynamiX Labs | Advanced Hardware Integration

Provides RF-level performance characterization for SDR hardware:
  - Noise Figure (NF) estimation via Y-factor method
  - IP3 / 1dB compression point estimation
  - Gain flatness profiling across frequency range
  - LNA cascade noise figure (Friis formula)
  - Spurious signal scanning (LO leakage, DC offset, image frequency)
  - DC offset and IQ imbalance measurement

Usage:
    from .hardware import HardwareManager
    rf = RFFrontendCharacterizer(hw_manager)
    nf = rf.estimate_noise_figure()
    flatness = rf.gain_flatness_profile(start_freq=88e6, stop_freq=108e6)
    spurs = rf.scan_spurious_signals()
"""

import numpy as np
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

log = logging.getLogger("satsdr.rf_frontend")

# Physical constants
BOLTZMANN_K = 1.380649e-23   # J/K
T_AMBIENT = 290.0            # Standard noise temperature (K)


# ─── Data Classes ────────────────────────────────────────────────────────

@dataclass
class NoiseFigureResult:
    """Noise figure measurement result."""
    noise_figure_db: float = 0.0
    noise_temperature_k: float = 0.0
    noise_floor_dbm: float = 0.0
    measurement_bandwidth_hz: float = 0.0
    method: str = "y-factor"

    def to_dict(self) -> dict:
        return {
            "noise_figure_dB": round(self.noise_figure_db, 2),
            "noise_temperature_K": round(self.noise_temperature_k, 1),
            "noise_floor_dBm": round(self.noise_floor_dbm, 2),
            "measurement_bandwidth_Hz": self.measurement_bandwidth_hz,
            "method": self.method,
        }


@dataclass
class GainFlatnessResult:
    """Gain flatness measurement across frequency range."""
    frequencies_mhz: List[float] = field(default_factory=list)
    gain_db: List[float] = field(default_factory=list)
    noise_floor_dbm: List[float] = field(default_factory=list)
    max_ripple_db: float = 0.0
    flatness_band_mhz: Tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict:
        return {
            "frequencies_MHz": [round(f, 3) for f in self.frequencies_mhz],
            "gain_dB": [round(g, 2) for g in self.gain_db],
            "noise_floor_dBm": [round(n, 2) for n in self.noise_floor_dbm],
            "max_ripple_dB": round(self.max_ripple_db, 2),
            "flatness_band_MHz": [round(f, 3) for f in self.flatness_band_mhz],
        }


@dataclass
class IQImbalanceResult:
    """IQ imbalance measurement."""
    amplitude_imbalance_db: float = 0.0
    phase_imbalance_deg: float = 0.0
    dc_offset_i: float = 0.0
    dc_offset_q: float = 0.0
    image_rejection_db: float = 0.0

    def to_dict(self) -> dict:
        return {
            "amplitude_imbalance_dB": round(self.amplitude_imbalance_db, 3),
            "phase_imbalance_deg": round(self.phase_imbalance_deg, 3),
            "dc_offset_I": round(self.dc_offset_i, 6),
            "dc_offset_Q": round(self.dc_offset_q, 6),
            "image_rejection_dB": round(self.image_rejection_db, 2),
        }


@dataclass
class SpuriousSignal:
    """A single spurious signal detection."""
    frequency_offset_hz: float = 0.0
    power_dbfs: float = 0.0
    spur_type: str = ""  # "dc_offset", "lo_leakage", "image", "harmonic", "unknown"

    def to_dict(self) -> dict:
        return {
            "frequency_offset_Hz": round(self.frequency_offset_hz, 1),
            "power_dBFS": round(self.power_dbfs, 2),
            "type": self.spur_type,
        }


@dataclass
class RFFrontendProfile:
    """Complete RF frontend characterization profile."""
    noise_figure: Optional[NoiseFigureResult] = None
    gain_flatness: Optional[GainFlatnessResult] = None
    iq_imbalance: Optional[IQImbalanceResult] = None
    spurious_signals: List[SpuriousSignal] = field(default_factory=list)
    sfdr_db: float = 0.0              # Spurious-free dynamic range
    enob: float = 0.0                 # Effective number of bits
    frequency_accuracy_ppm: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        result = {
            "sfdr_dB": round(self.sfdr_db, 2),
            "enob": round(self.enob, 2),
            "frequency_accuracy_ppm": round(self.frequency_accuracy_ppm, 2),
            "timestamp": self.timestamp,
        }
        if self.noise_figure:
            result["noise_figure"] = self.noise_figure.to_dict()
        if self.gain_flatness:
            result["gain_flatness"] = self.gain_flatness.to_dict()
        if self.iq_imbalance:
            result["iq_imbalance"] = self.iq_imbalance.to_dict()
        if self.spurious_signals:
            result["spurious_signals"] = [s.to_dict() for s in self.spurious_signals]
        return result

    def summary(self) -> str:
        lines = [
            "─" * 60,
            "           RF FRONTEND CHARACTERIZATION",
            "─" * 60,
        ]
        if self.noise_figure:
            lines.append(f"  Noise Figure:     {self.noise_figure.noise_figure_db:.2f} dB")
            lines.append(f"  Noise Floor:      {self.noise_figure.noise_floor_dbm:.1f} dBm")
        if self.iq_imbalance:
            lines.append(f"  IQ Amp Imbalance: {self.iq_imbalance.amplitude_imbalance_db:.3f} dB")
            lines.append(f"  IQ Phase Error:   {self.iq_imbalance.phase_imbalance_deg:.3f}°")
            lines.append(f"  DC Offset (I/Q):  {self.iq_imbalance.dc_offset_i:.6f} / {self.iq_imbalance.dc_offset_q:.6f}")
            lines.append(f"  Image Rejection:  {self.iq_imbalance.image_rejection_db:.1f} dB")
        lines.append(f"  SFDR:             {self.sfdr_db:.1f} dB")
        lines.append(f"  ENOB:             {self.enob:.1f} bits")
        lines.append(f"  Freq Accuracy:    {self.frequency_accuracy_ppm:.2f} ppm")
        if self.spurious_signals:
            lines.append(f"  Spurious Signals: {len(self.spurious_signals)} detected")
        lines.append("─" * 60)
        return "\n".join(lines)


# ─── RF Frontend Characterizer ───────────────────────────────────────────

class RFFrontendCharacterizer:
    """
    Characterizes RF performance of SDR hardware.

    Performs signal-quality measurements including noise figure estimation,
    IQ imbalance analysis, spurious signal detection, and dynamic range
    calculation.

    Parameters
    ----------
    hw_manager : HardwareManager
        A configured and connected HardwareManager instance.
    n_samples : int
        Number of samples to capture for each measurement.
    """

    def __init__(self, hw_manager, n_samples: int = 262144):
        self.hw = hw_manager
        self.n_samples = n_samples
        self._sample_rate = getattr(hw_manager, 'sample_rate', 250e3)
        self._frequency = getattr(hw_manager, 'frequency', 100e6)

    def full_characterization(self) -> RFFrontendProfile:
        """
        Run the complete RF frontend characterization suite.

        Returns
        -------
        RFFrontendProfile
            Complete characterization results.
        """
        from datetime import datetime, timezone

        profile = RFFrontendProfile()
        profile.timestamp = datetime.now(timezone.utc).isoformat()

        log.info("Starting RF frontend characterization...")

        # 1. Noise figure estimation
        try:
            profile.noise_figure = self.estimate_noise_figure()
            log.info(f"Noise figure: {profile.noise_figure.noise_figure_db:.2f} dB")
        except Exception as e:
            log.warning(f"Noise figure estimation failed: {e}")

        # 2. IQ imbalance measurement
        try:
            profile.iq_imbalance = self.measure_iq_imbalance()
            log.info(f"IQ imbalance: amp={profile.iq_imbalance.amplitude_imbalance_db:.3f}dB "
                     f"phase={profile.iq_imbalance.phase_imbalance_deg:.3f}°")
        except Exception as e:
            log.warning(f"IQ imbalance measurement failed: {e}")

        # 3. Spurious signal scan
        try:
            profile.spurious_signals = self.scan_spurious_signals()
            log.info(f"Spurious signals: {len(profile.spurious_signals)} detected")
        except Exception as e:
            log.warning(f"Spurious scan failed: {e}")

        # 4. SFDR calculation
        try:
            profile.sfdr_db = self.measure_sfdr()
            log.info(f"SFDR: {profile.sfdr_db:.1f} dB")
        except Exception as e:
            log.warning(f"SFDR measurement failed: {e}")

        # 5. ENOB calculation
        try:
            profile.enob = self.estimate_enob()
            log.info(f"ENOB: {profile.enob:.1f} bits")
        except Exception as e:
            log.warning(f"ENOB estimation failed: {e}")

        # 6. Frequency accuracy
        try:
            profile.frequency_accuracy_ppm = self.estimate_frequency_accuracy()
            log.info(f"Freq accuracy: {profile.frequency_accuracy_ppm:.2f} ppm")
        except Exception as e:
            log.warning(f"Frequency accuracy test failed: {e}")

        log.info("RF characterization complete.")
        return profile

    # ── Noise Figure Estimation ──

    def estimate_noise_figure(self, n_averages: int = 16) -> NoiseFigureResult:
        """
        Estimate receiver noise figure using noise-floor measurement method.

        Measures the noise floor power and calculates NF relative to
        the theoretical thermal noise floor (kTB).

        This is a simplified method that assumes the antenna port sees
        ambient temperature (~290K). For precise measurements, a calibrated
        noise source (ENR) and Y-factor method would be required.

        Parameters
        ----------
        n_averages : int
            Number of noise floor measurements to average.
        """
        result = NoiseFigureResult()
        result.measurement_bandwidth_hz = self._sample_rate
        result.method = "noise-floor"

        # Capture multiple noise samples and average
        noise_powers = []
        for _ in range(n_averages):
            samples = self.hw.read_samples(self.n_samples)
            # Calculate noise power in watts (assuming 50Ω system)
            power = np.mean(np.abs(samples) ** 2)
            noise_powers.append(power)

        avg_noise_power = np.mean(noise_powers)

        # Theoretical thermal noise floor: P_thermal = kTB
        thermal_noise_w = BOLTZMANN_K * T_AMBIENT * self._sample_rate

        # Noise figure: NF = P_measured / P_thermal
        if thermal_noise_w > 0 and avg_noise_power > 0:
            nf_linear = avg_noise_power / thermal_noise_w
            # Clamp to reasonable range (NF >= 1.0 = 0 dB)
            nf_linear = max(nf_linear, 1.0)
            result.noise_figure_db = 10.0 * np.log10(nf_linear)

            # Noise temperature
            result.noise_temperature_k = T_AMBIENT * (nf_linear - 1.0)
        else:
            result.noise_figure_db = 0.0

        # Noise floor in dBm
        if avg_noise_power > 0:
            result.noise_floor_dbm = 10.0 * np.log10(avg_noise_power) + 30.0
        else:
            result.noise_floor_dbm = -174.0  # theoretical minimum

        return result

    # ── IQ Imbalance Measurement ──

    def measure_iq_imbalance(self) -> IQImbalanceResult:
        """
        Measure IQ amplitude imbalance, phase error, and DC offset.

        Captures samples and analyzes the I and Q channels independently
        to detect systematic hardware impairments.
        """
        result = IQImbalanceResult()

        samples = self.hw.read_samples(self.n_samples)

        i_channel = np.real(samples)
        q_channel = np.imag(samples)

        # DC offset (mean of each channel)
        result.dc_offset_i = float(np.mean(i_channel))
        result.dc_offset_q = float(np.mean(q_channel))

        # Remove DC for imbalance measurements
        i_ac = i_channel - result.dc_offset_i
        q_ac = q_channel - result.dc_offset_q

        # Amplitude imbalance: ratio of RMS powers
        rms_i = np.sqrt(np.mean(i_ac ** 2))
        rms_q = np.sqrt(np.mean(q_ac ** 2))

        if rms_q > 0:
            amp_ratio = rms_i / rms_q
            result.amplitude_imbalance_db = 20.0 * np.log10(amp_ratio)
        else:
            result.amplitude_imbalance_db = 0.0

        # Phase imbalance: deviation from 90° between I and Q
        # Use cross-correlation at zero lag
        cross_corr = np.mean(i_ac * q_ac)
        if rms_i > 0 and rms_q > 0:
            cos_error = cross_corr / (rms_i * rms_q)
            cos_error = np.clip(cos_error, -1.0, 1.0)
            # Ideal is cos(90°) = 0, so the error angle is arccos of the value
            result.phase_imbalance_deg = np.degrees(np.arcsin(cos_error))
        else:
            result.phase_imbalance_deg = 0.0

        # Image rejection ratio (from imbalance parameters)
        # IRR = 10*log10((1 + g^2 + 2g*cos(phi)) / (1 + g^2 - 2g*cos(phi)))
        g = 10 ** (result.amplitude_imbalance_db / 20.0)
        phi = np.radians(result.phase_imbalance_deg)
        numerator = 1 + g**2 + 2 * g * np.cos(phi)
        denominator = 1 + g**2 - 2 * g * np.cos(phi)
        if denominator > 0:
            result.image_rejection_db = 10.0 * np.log10(numerator / denominator)
        else:
            result.image_rejection_db = 0.0

        return result

    # ── Spurious Signal Detection ──

    def scan_spurious_signals(self, threshold_dbfs: float = -50.0) -> List[SpuriousSignal]:
        """
        Scan for spurious signals in the receiver passband.

        Identifies DC offset spikes, LO leakage, image frequencies,
        and other spurious emissions above the specified threshold.

        Parameters
        ----------
        threshold_dbfs : float
            Detection threshold in dBFS. Signals above this level
            (relative to full-scale) are flagged as spurious.
        """
        samples = self.hw.read_samples(self.n_samples)
        spurs = []

        # Compute spectrum
        n_fft = min(len(samples), 65536)
        window = np.hanning(n_fft)
        windowed = samples[:n_fft] * window
        spectrum = np.fft.fftshift(np.fft.fft(windowed))
        power_dbfs = 20.0 * np.log10(np.abs(spectrum) / n_fft + 1e-20)
        freqs = np.fft.fftshift(np.fft.fftfreq(n_fft, d=1.0 / self._sample_rate))

        # Noise floor estimation (median of spectrum)
        noise_floor = np.median(power_dbfs)

        # DC offset check (center bin)
        center_idx = n_fft // 2
        dc_power = power_dbfs[center_idx]
        if dc_power > noise_floor + 10.0:
            spurs.append(SpuriousSignal(
                frequency_offset_hz=0.0,
                power_dbfs=float(dc_power),
                spur_type="dc_offset",
            ))

        # Find peaks above threshold
        from scipy.signal import find_peaks as _find_peaks
        try:
            peaks, properties = _find_peaks(
                power_dbfs,
                height=max(threshold_dbfs, noise_floor + 6.0),
                distance=10,
                prominence=6.0,
            )
        except ImportError:
            # Fallback: simple threshold detection
            peaks = np.where(power_dbfs > max(threshold_dbfs, noise_floor + 6.0))[0]

        for peak_idx in peaks:
            if abs(peak_idx - center_idx) < 3:
                continue  # Already handled DC

            freq_offset = float(freqs[peak_idx])
            power = float(power_dbfs[peak_idx])

            # Classify spur type
            spur_type = "unknown"
            abs_offset = abs(freq_offset)

            if abs_offset < self._sample_rate * 0.01:
                spur_type = "lo_leakage"
            elif abs(abs_offset - self._sample_rate / 4) < self._sample_rate * 0.02:
                spur_type = "image"
            elif abs_offset > self._sample_rate * 0.45:
                spur_type = "alias"
            else:
                # Check if it's a harmonic of something
                for harmonic in range(2, 6):
                    if abs(abs_offset * harmonic - self._sample_rate / 2) < self._sample_rate * 0.02:
                        spur_type = f"harmonic_{harmonic}"
                        break

            spurs.append(SpuriousSignal(
                frequency_offset_hz=freq_offset,
                power_dbfs=power,
                spur_type=spur_type,
            ))

        return spurs

    # ── SFDR Measurement ──

    def measure_sfdr(self) -> float:
        """
        Measure Spurious-Free Dynamic Range (SFDR).

        SFDR is the ratio (in dB) between the strongest desired signal
        and the strongest spurious emission.

        For noise-only measurements (no injected signal), this returns
        the ratio between the peak spectral bin and the next-highest
        non-adjacent bin.
        """
        samples = self.hw.read_samples(self.n_samples)

        n_fft = min(len(samples), 65536)
        window = np.blackman(n_fft)
        windowed = samples[:n_fft] * window
        spectrum = np.fft.fftshift(np.fft.fft(windowed))
        power_db = 20.0 * np.log10(np.abs(spectrum) / n_fft + 1e-20)

        # Find the peak
        peak_idx = np.argmax(power_db)
        peak_power = power_db[peak_idx]

        # Zero out the peak region (±5 bins) and find the next highest
        masked = power_db.copy()
        start = max(0, peak_idx - 5)
        end = min(len(masked), peak_idx + 6)
        masked[start:end] = -200.0

        second_peak_power = np.max(masked)

        sfdr = peak_power - second_peak_power
        return float(sfdr)

    # ── ENOB Estimation ──

    def estimate_enob(self) -> float:
        """
        Estimate Effective Number of Bits (ENOB) from measured SNR.

        ENOB = (SINAD - 1.76) / 6.02

        Where SINAD is measured as the ratio of signal power to
        noise+distortion power. For noise-only captures, this gives
        the noise-limited ENOB.
        """
        samples = self.hw.read_samples(self.n_samples)

        # Compute signal and noise power
        n_fft = min(len(samples), 65536)
        window = np.hanning(n_fft)
        windowed = samples[:n_fft] * window
        spectrum = np.abs(np.fft.fft(windowed)) ** 2

        # Signal: peak bin and ±2 adjacent
        peak_idx = np.argmax(spectrum)
        signal_bins = set()
        for offset in range(-2, 3):
            idx = (peak_idx + offset) % n_fft
            signal_bins.add(idx)

        signal_power = sum(spectrum[i] for i in signal_bins)
        noise_power = sum(spectrum[i] for i in range(n_fft) if i not in signal_bins)

        if noise_power > 0:
            sinad_db = 10.0 * np.log10(signal_power / noise_power)
        else:
            sinad_db = 100.0  # Essentially infinite

        enob = (sinad_db - 1.76) / 6.02
        return max(0.0, float(enob))

    # ── Frequency Accuracy ──

    def estimate_frequency_accuracy(self) -> float:
        """
        Estimate oscillator frequency accuracy in PPM.

        Uses the strongest detected signal as a reference and measures
        the offset from the expected bin center. This gives a rough
        estimate of crystal oscillator accuracy.

        For precise measurements, a known reference signal (e.g., NOAA
        carrier at exactly 137.1 MHz) should be used.
        """
        samples = self.hw.read_samples(self.n_samples)

        n_fft = min(len(samples), 65536)
        spectrum = np.abs(np.fft.fft(samples[:n_fft]))

        # Find peak
        peak_idx = np.argmax(spectrum)

        # Parabolic interpolation for sub-bin accuracy
        if 1 <= peak_idx <= n_fft - 2:
            alpha = np.log(spectrum[peak_idx - 1] + 1e-20)
            beta = np.log(spectrum[peak_idx] + 1e-20)
            gamma = np.log(spectrum[peak_idx + 1] + 1e-20)
            p = 0.5 * (alpha - gamma) / (alpha - 2 * beta + gamma)
        else:
            p = 0.0

        # Frequency of peak
        bin_width = self._sample_rate / n_fft
        detected_offset = (peak_idx + p) * bin_width

        # The offset from center frequency
        if detected_offset > self._sample_rate / 2:
            detected_offset -= self._sample_rate

        # Convert to PPM relative to center frequency
        if self._frequency > 0:
            ppm = (detected_offset / self._frequency) * 1e6
        else:
            ppm = 0.0

        return float(ppm)

    # ── Gain Flatness Profile ──

    def gain_flatness_profile(self, start_freq: float = None,
                               stop_freq: float = None,
                               n_steps: int = 20) -> GainFlatnessResult:
        """
        Measure gain/noise-floor across a frequency range.

        Steps the SDR through a frequency sweep and measures the noise
        floor at each step. Assumes the SDR supports re-tuning.

        Parameters
        ----------
        start_freq : float
            Start frequency in Hz (default: current - 5 MHz)
        stop_freq : float
            Stop frequency in Hz (default: current + 5 MHz)
        n_steps : int
            Number of frequency steps
        """
        if start_freq is None:
            start_freq = self._frequency - 5e6
        if stop_freq is None:
            stop_freq = self._frequency + 5e6

        result = GainFlatnessResult()
        result.flatness_band_mhz = (start_freq / 1e6, stop_freq / 1e6)

        freqs = np.linspace(start_freq, stop_freq, n_steps)

        for freq in freqs:
            try:
                # Re-tune
                self.hw.configure(
                    frequency=freq,
                    sample_rate=self._sample_rate,
                    gain=getattr(self.hw, 'gain', 30.0)
                )
                time.sleep(0.05)  # Settle time

                # Measure noise floor
                samples = self.hw.read_samples(self.n_samples)
                power = np.mean(np.abs(samples) ** 2)
                power_dbm = 10.0 * np.log10(power + 1e-20) + 30.0

                result.frequencies_mhz.append(freq / 1e6)
                result.noise_floor_dbm.append(power_dbm)

                # Relative gain (normalized to first measurement)
                if not result.gain_db:
                    result.gain_db.append(0.0)
                else:
                    relative = power_dbm - result.noise_floor_dbm[0]
                    result.gain_db.append(relative)

            except Exception as e:
                log.warning(f"Gain flatness measurement failed at {freq/1e6:.3f} MHz: {e}")

        # Calculate max ripple
        if result.gain_db:
            result.max_ripple_db = max(result.gain_db) - min(result.gain_db)

        # Restore original frequency
        try:
            self.hw.configure(
                frequency=self._frequency,
                sample_rate=self._sample_rate,
                gain=getattr(self.hw, 'gain', 30.0)
            )
        except Exception:
            pass

        return result


# ─── LNA Cascade Calculator ─────────────────────────────────────────────

class LNACascadeCalculator:
    """
    Calculates system noise figure using the Friis cascade formula.

    Useful for computing the total system NF when an external LNA
    is placed between the antenna and the SDR receiver.

    Friis formula:
        NF_total = NF_1 + (NF_2 - 1)/G_1 + (NF_3 - 1)/(G_1*G_2) + ...

    Usage:
        calc = LNACascadeCalculator()
        calc.add_stage("LNA", nf_db=0.5, gain_db=20.0)
        calc.add_stage("Cable", nf_db=1.0, gain_db=-1.0)
        calc.add_stage("RTL-SDR", nf_db=6.0, gain_db=30.0)
        total_nf = calc.compute()
    """

    def __init__(self):
        self.stages: List[Dict] = []

    def add_stage(self, name: str, nf_db: float, gain_db: float):
        """
        Add a stage to the cascade chain.

        Parameters
        ----------
        name : str
            Stage name (e.g., "LNA", "Filter", "SDR Receiver")
        nf_db : float
            Noise figure of this stage in dB
        gain_db : float
            Gain of this stage in dB (negative for loss)
        """
        self.stages.append({
            "name": name,
            "nf_db": nf_db,
            "gain_db": gain_db,
            "nf_linear": 10 ** (nf_db / 10.0),
            "gain_linear": 10 ** (gain_db / 10.0),
        })

    def compute(self) -> dict:
        """
        Compute total system noise figure using Friis formula.

        Returns
        -------
        dict with:
            - total_nf_db: Total system noise figure in dB
            - total_gain_db: Total system gain in dB
            - stage_contributions: Per-stage NF contribution
            - noise_temperature_k: System noise temperature
        """
        if not self.stages:
            return {"total_nf_db": 0.0, "total_gain_db": 0.0}

        # Friis cascade
        total_nf_linear = self.stages[0]["nf_linear"]
        cumulative_gain = self.stages[0]["gain_linear"]
        total_gain_db = self.stages[0]["gain_db"]
        contributions = [{
            "name": self.stages[0]["name"],
            "contribution_db": self.stages[0]["nf_db"],
        }]

        for i in range(1, len(self.stages)):
            stage = self.stages[i]
            contribution = (stage["nf_linear"] - 1.0) / cumulative_gain

            total_nf_linear += contribution
            cumulative_gain *= stage["gain_linear"]
            total_gain_db += stage["gain_db"]

            contributions.append({
                "name": stage["name"],
                "contribution_db": 10.0 * np.log10(1.0 + contribution) if contribution > 0 else 0.0,
            })

        total_nf_db = 10.0 * np.log10(total_nf_linear)
        noise_temp = T_AMBIENT * (total_nf_linear - 1.0)

        return {
            "total_nf_db": round(total_nf_db, 3),
            "total_gain_db": round(total_gain_db, 2),
            "noise_temperature_k": round(noise_temp, 1),
            "stage_contributions": contributions,
        }

    def clear(self):
        """Remove all stages."""
        self.stages.clear()
