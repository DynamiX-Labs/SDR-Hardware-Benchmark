"""
Abstract Base Decoder
All satellite decoders inherit from this class.
DynamiX Labs
"""

from abc import ABC, abstractmethod
from pathlib import Path
import numpy as np
import logging


class BaseDecoder(ABC):
    """Abstract base class for all satellite signal decoders."""

    NAME: str = "base"
    FREQUENCY: float = 0.0       # Default center frequency (Hz)
    MODULATION: str = "unknown"  # FM, BPSK, QPSK, GMSK, etc.
    BAUDRATE: int = 0            # Symbol rate (baud)
    BANDWIDTH: float = 0.0       # Required RF bandwidth (Hz)

    def __init__(self, sample_rate: float = 250_000, output_dir: str = "./output"):
        self.sample_rate = sample_rate
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger(f"satsdr.{self.NAME}")

    @abstractmethod
    def decode(self, samples: np.ndarray) -> dict:
        """
        Decode IQ samples into structured data.

        Args:
            samples: Complex64 IQ samples

        Returns:
            Dictionary with decoded payload
        """
        ...

    @abstractmethod
    def format_output(self, decoded: dict) -> str:
        """Format decoded data for display or storage."""
        ...

    def decode_file(self, path: str, chunk_size: int = 65536) -> list:
        """Decode from an IQ binary file (complex64 format)."""
        results = []
        self.log.info(f"Decoding file: {path}")
        with open(path, "rb") as f:
            while True:
                raw = f.read(chunk_size * 8)  # 8 bytes per complex64
                if not raw:
                    break
                samples = np.frombuffer(raw, dtype=np.complex64)
                result = self.decode(samples)
                if result:
                    results.append(result)
                    self.log.debug(self.format_output(result))
        return results

    def decode_live(self, hardware, chunk_size: int = 65536):
        """Decode from live SDR hardware stream."""
        self.log.info(f"Live decode started | {self.FREQUENCY/1e6:.3f} MHz")
        try:
            while True:
                samples = hardware.read_samples(chunk_size)
                result = self.decode(samples)
                if result:
                    print(self.format_output(result))
        except KeyboardInterrupt:
            self.log.info("Decode stopped by user")
