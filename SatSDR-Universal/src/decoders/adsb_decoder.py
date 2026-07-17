"""
ADS-B (Automatic Dependent Surveillance-Broadcast) Decoder
Decodes 1090 MHz aviation transponder signals (Mode-S Extended Squitter).
DynamiX Labs
"""

import numpy as np
from datetime import datetime
import json
from .base_decoder import BaseDecoder


class ADSBDecoder(BaseDecoder):
    """
    ADS-B 1090ES Decoder.

    Protocol:   Mode-S Extended Squitter
    Frequency:  1090 MHz
    Modulation: PPM (Pulse Position Modulation)
    Chip rate:  1 Mchip/s
    Frame:      Short (56 bits) or Long (112 bits)
    """

    NAME = "adsb"
    FREQUENCY = 1_090_000_000
    MODULATION = "PPM"
    BAUDRATE = 1_000_000
    BANDWIDTH = 4_000_000  # 4 MHz

    # Mode-S CRC generator polynomial
    GENERATOR = 0xFFF409

    # DF type → description
    DF_TYPES = {
        0:  "Short ACAS",
        4:  "Surveillance Altitude",
        5:  "Surveillance Identity",
        11: "All-Call Reply",
        17: "ADS-B (Extended Squitter)",
        18: "TIS-B",
        19: "Military Extended Squitter",
        20: "Comm-B Altitude",
        21: "Comm-B Identity",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.aircraft: dict = {}  # ICAO → aircraft state
        self.message_count = 0

    def _ppm_demodulate(self, samples: np.ndarray) -> list:
        """Detect PPM pulses and extract bit stream."""
        # Magnitude
        mag = np.abs(samples)

        # Simple threshold detection — 4x noise floor
        threshold = mag.mean() * 4.0
        high = (mag > threshold).astype(np.int8)

        # Find rising edges (Mode-S preamble: 0.5μs pulses at 0, 1, 3.5, 4.5 μs)
        edges = np.where(np.diff(high) > 0)[0]

        messages = []
        sps = max(1, int(self.sample_rate / 1e6))  # samples per chip

        for edge in edges:
            if edge + 120 * sps >= len(mag):
                break
            # Check preamble pattern
            window = mag[edge:edge + 10 * sps]
            if len(window) < 10 * sps:
                continue
            # Extract 112-bit payload (Long frame)
            bits = []
            for i in range(112):
                idx = edge + (8 + i * 2) * sps  # after 8μs preamble
                if idx + sps < len(mag):
                    chip0 = mag[idx:idx + sps].mean()
                    chip1 = mag[idx + sps:idx + 2 * sps].mean()
                    bits.append(1 if chip0 > chip1 else 0)
            if len(bits) == 112:
                messages.append(bits)
        return messages

    def _bits_to_int(self, bits: list, start: int, length: int) -> int:
        val = 0
        for b in bits[start:start + length]:
            val = (val << 1) | b
        return val

    def _decode_message(self, bits: list) -> dict:
        """Decode a single Mode-S message."""
        df = self._bits_to_int(bits, 0, 5)
        icao = self._bits_to_int(bits, 8, 24)
        icao_hex = f"{icao:06X}"

        msg = {
            "icao": icao_hex,
            "df": df,
            "df_type": self.DF_TYPES.get(df, f"DF{df}"),
            "timestamp": datetime.utcnow().isoformat(),
        }

        if df == 17:  # ADS-B Extended Squitter
            type_code = self._bits_to_int(bits, 32, 5)
            msg["type_code"] = type_code

            # Aircraft ID (TC 1–4)
            if 1 <= type_code <= 4:
                charset = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######"
                callsign = ""
                for i in range(8):
                    idx = self._bits_to_int(bits, 40 + i * 6, 6)
                    callsign += charset[idx] if idx < len(charset) else "?"
                msg["callsign"] = callsign.strip()

            # Airborne position (TC 9–18)
            elif 9 <= type_code <= 18:
                altitude_raw = self._bits_to_int(bits, 40, 13)
                if altitude_raw:
                    altitude = (altitude_raw - 1000)  # feet MSL (simplified)
                    msg["altitude_ft"] = altitude

            # Airborne velocity (TC 19)
            elif type_code == 19:
                vew = self._bits_to_int(bits, 46, 10) - 512  # East-West velocity
                vns = self._bits_to_int(bits, 56, 10) - 512  # North-South velocity
                speed = (vew ** 2 + vns ** 2) ** 0.5
                msg["speed_kts"] = round(speed)
                msg["vew_kts"] = vew
                msg["vns_kts"] = vns

        # Update aircraft state
        if icao_hex not in self.aircraft:
            self.aircraft[icao_hex] = {}
        self.aircraft[icao_hex].update(
            {k: v for k, v in msg.items() if k not in ("timestamp", "df", "df_type")}
        )
        return msg

    def decode(self, samples: np.ndarray) -> dict:
        """Decode ADS-B messages from IQ samples."""
        if len(samples) < 1000:
            return {}

        messages_bits = self._ppm_demodulate(samples)
        decoded_messages = []

        for bits in messages_bits:
            try:
                msg = self._decode_message(bits)
                decoded_messages.append(msg)
                self.message_count += 1
            except Exception:
                pass

        if not decoded_messages:
            return {}

        return {
            "decoder": "ADS-B",
            "messages": decoded_messages,
            "aircraft_count": len(self.aircraft),
            "total_messages": self.message_count,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def get_aircraft_table(self) -> list:
        """Return current aircraft state table."""
        return [
            {"icao": k, **v}
            for k, v in sorted(self.aircraft.items())
        ]

    def format_output(self, decoded: dict) -> str:
        msgs = decoded.get("messages", [])
        lines = [f"[ADS-B] {decoded['timestamp']} | {decoded['aircraft_count']} aircraft | {decoded['total_messages']} msgs"]
        for m in msgs[:5]:
            cs = m.get("callsign", "?????")
            alt = m.get("altitude_ft", "")
            spd = m.get("speed_kts", "")
            lines.append(f"  ICAO:{m['icao']} CS:{cs:8s} Alt:{alt:6} Spd:{spd}")
        return "\n".join(lines)
