"""
SDR Hardware Abstraction Layer
Unified interface for RTL-SDR, HackRF, PlutoSDR, USRP.
DynamiX Labs
"""

import numpy as np
import logging
from typing import Optional

log = logging.getLogger("satsdr.hardware")


class HardwareManager:
    """
    Unified SDR hardware interface using SoapySDR.

    Supported:
        rtlsdr    — RTL2832U-based dongles (RTL-SDR v3, Nooelec)
        hackrf    — HackRF One
        pluto     — ADALM-PLUTO (PlutoSDR)
        usrp_b200 — USRP B200
        usrp_b210 — USRP B210

    Example:
        hw = HardwareManager("rtlsdr")
        hw.configure(frequency=137.5e6, sample_rate=250e3, gain=30)
        samples = hw.read_samples(65536)
        hw.close()
    """

    DRIVER_MAP = {
        "rtlsdr":   "rtlsdr",
        "hackrf":   "hackrf",
        "pluto":    "plutosdr",
        "usrp_b200": "uhd",
        "usrp_b210": "uhd",
    }

    MAX_GAIN = {
        "rtlsdr":   49.6,
        "hackrf":   62.0,
        "pluto":    73.0,
        "usrp_b200": 76.0,
        "usrp_b210": 76.0,
    }

    def __init__(self, hardware: str, device_index: int = 0):
        self.hardware = hardware
        self.device_index = device_index
        self._sdr = None
        self._driver = self.DRIVER_MAP.get(hardware, hardware)
        self._connect()

    def _connect(self):
        """Open SoapySDR device."""
        try:
            import SoapySDR
            from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
            args = f"driver={self._driver}"
            if self.hardware == "rtlsdr":
                args += f",rtl={self.device_index}"
            self._sdr = SoapySDR.Device(args)
            self._rx_stream = None
            self._SoapySDR = SoapySDR
            self._SOAPY_SDR_RX = SOAPY_SDR_RX
            self._SOAPY_SDR_CF32 = SOAPY_SDR_CF32
            log.info(f"Connected: {self.hardware} ({self._driver})")
        except ImportError:
            log.warning("SoapySDR not installed — using simulated hardware")
            self._sdr = None
        except Exception as e:
            log.error(f"Hardware connection failed: {e}")
            self._sdr = None

    def configure(self, frequency: float, sample_rate: float, gain: float = 30.0):
        """Configure hardware parameters."""
        self.frequency = frequency
        self.sample_rate = sample_rate
        self.gain = min(gain, self.MAX_GAIN.get(self.hardware, 60.0))

        if self._sdr is None:
            log.info(f"[SIM] Config: freq={frequency/1e6:.3f}MHz rate={sample_rate/1e3:.1f}kSPS gain={gain}dB")
            return

        self._sdr.setFrequency(self._SOAPY_SDR_RX, 0, frequency)
        self._sdr.setSampleRate(self._SOAPY_SDR_RX, 0, sample_rate)
        self._sdr.setGain(self._SOAPY_SDR_RX, 0, self.gain)

        self._rx_stream = self._sdr.setupStream(self._SOAPY_SDR_RX, self._SOAPY_SDR_CF32)
        self._sdr.activateStream(self._rx_stream)
        log.info(f"Configured: {frequency/1e6:.3f} MHz | {sample_rate/1e3:.1f} kSPS | {self.gain} dB")

    def read_samples(self, n: int = 65536) -> np.ndarray:
        """Read n complex samples from hardware."""
        if self._sdr is None:
            # Simulate noise + carrier for testing
            t = np.arange(n) / self.sample_rate
            carrier = np.exp(2j * np.pi * 1000 * t)
            noise = (np.random.randn(n) + 1j * np.random.randn(n)) * 0.1
            return (carrier + noise).astype(np.complex64)

        buf = np.zeros(n, dtype=np.complex64)
        sr = self._sdr.readStream(self._rx_stream, [buf], n)
        return buf[:sr.ret] if sr.ret > 0 else buf

    def close(self):
        """Release hardware resources."""
        if self._sdr and self._rx_stream:
            self._sdr.deactivateStream(self._rx_stream)
            self._sdr.closeStream(self._rx_stream)
        log.info("Hardware closed")

    def __del__(self):
        self.close()
