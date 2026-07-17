"""
NOAA APT (Automatic Picture Transmission) Decoder
Decodes 137 MHz weather satellite images.
DynamiX Labs
"""

import numpy as np
from scipy.signal import butter, lfilter, resample_poly
from PIL import Image
from datetime import datetime
import json
from .base_decoder import BaseDecoder


class APTDecoder(BaseDecoder):
    """
    NOAA APT Decoder — produces 2400-line weather images.

    Satellites: NOAA-15 (137.620 MHz), NOAA-18 (137.9125 MHz),
                NOAA-19 (137.100 MHz)
    Modulation: WBFM, subcarrier 2400 Hz AM
    Line rate:  2 lines/second = 4160 px/line
    """

    NAME = "apt"
    FREQUENCY = 137_500_000  # Default; override per satellite
    MODULATION = "WBFM"
    BAUDRATE = 4160
    BANDWIDTH = 40_000       # 40 kHz RF bandwidth

    APT_LINE_RATE = 2        # lines per second
    APT_SUBCARRIER = 2400    # Hz
    APT_LINE_WIDTH = 2080    # samples per line at 4160 SPS

    SATELLITE_FREQS = {
        "NOAA-15": 137.620e6,
        "NOAA-18": 137.9125e6,
        "NOAA-19": 137.100e6,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.lines = []
        self.output_sample_rate = 20800  # 2x Nyquist for 2x2080px

    def _fm_demodulate(self, samples: np.ndarray) -> np.ndarray:
        """Wideband FM demodulation via differentiation."""
        phase = np.angle(samples)
        demod = np.diff(np.unwrap(phase))
        return demod / np.pi

    def _am_detect_subcarrier(self, audio: np.ndarray) -> np.ndarray:
        """AM envelope detect the 2400 Hz APT subcarrier."""
        # Bandpass around 2400 Hz subcarrier
        nyq = self.output_sample_rate / 2
        low = (APTDecoder.APT_SUBCARRIER - 400) / nyq
        high = (APTDecoder.APT_SUBCARRIER + 400) / nyq
        b, a = butter(4, [low, high], btype="band")
        filtered = lfilter(b, a, audio)
        # Envelope detection: rectify + lowpass
        envelope = np.abs(filtered)
        lp_b, lp_a = butter(4, 2400 / nyq)
        return lfilter(lp_b, lp_a, envelope)

    def _sync_lines(self, image_data: np.ndarray) -> np.ndarray:
        """Detect APT sync pattern and align scan lines."""
        sync_pattern = np.tile([0, 0, 1, 1] * 7 + [0, 0, 0, 0], 10)
        corr = np.correlate(image_data[:10000], sync_pattern, mode="valid")
        line_start = np.argmax(corr)
        samples_per_line = self.APT_LINE_WIDTH
        n_lines = (len(image_data) - line_start) // samples_per_line
        if n_lines < 10:
            return np.array([])
        return image_data[line_start:line_start + n_lines * samples_per_line].reshape(
            n_lines, samples_per_line
        )

    def decode(self, samples: np.ndarray) -> dict:
        """Decode APT IQ samples → image lines."""
        if len(samples) < self.APT_LINE_WIDTH * 2:
            return {}

        # 1. FM demodulate
        audio = self._fm_demodulate(samples)

        # 2. Resample to output rate
        in_rate = int(self.sample_rate)
        out_rate = self.output_sample_rate
        from math import gcd
        g = gcd(in_rate, out_rate)
        audio_resampled = resample_poly(audio, out_rate // g, in_rate // g)

        # 3. AM detect subcarrier
        image_signal = self._am_detect_subcarrier(audio_resampled)

        # 4. Normalize 0–255
        img_min, img_max = image_signal.min(), image_signal.max()
        if img_max > img_min:
            normalized = ((image_signal - img_min) / (img_max - img_min) * 255).astype(np.uint8)
        else:
            return {}

        # 5. Sync and reshape lines
        lines = self._sync_lines(normalized.astype(np.float32))
        if lines.size == 0:
            return {}

        self.lines.extend(lines.tolist())

        return {
            "decoder": "APT",
            "n_lines": len(lines),
            "timestamp": datetime.utcnow().isoformat(),
            "image_width": self.APT_LINE_WIDTH,
        }

    def save_image(self, filename: str = None) -> str:
        """Save accumulated scan lines as PNG."""
        if not self.lines:
            return ""
        arr = np.array(self.lines, dtype=np.uint8)
        img = Image.fromarray(arr, mode="L")
        if filename is None:
            filename = self.output_dir / f"apt_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        img.save(filename)
        self.log.info(f"Saved APT image: {filename} ({arr.shape[0]} lines)")
        return str(filename)

    def format_output(self, decoded: dict) -> str:
        return (
            f"[APT] {decoded.get('timestamp','?')} | "
            f"Lines: {decoded.get('n_lines', 0)} | "
            f"Total: {len(self.lines)}"
        )
