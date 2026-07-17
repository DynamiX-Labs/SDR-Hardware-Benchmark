"""
AX.25 Frame Parser
Decodes AX.25 UI frames used by amateur CubeSats.
DynamiX Labs
"""

import struct
from dataclasses import dataclass, field
from typing import Optional, List
import logging

log = logging.getLogger("cubesat.ax25")


@dataclass
class AX25Frame:
    """Parsed AX.25 frame."""
    destination: str = ""
    source: str = ""
    digipeaters: List[str] = field(default_factory=list)
    pid: int = 0xF0          # Protocol ID
    info: bytes = b""        # Information field (payload)
    frame_type: str = "UI"   # UI = Unnumbered Information
    raw: bytes = b""


class AX25Parser:
    """
    AX.25 frame parser.

    AX.25 Address Format (7 bytes each):
        Bytes 0-5: Callsign (6 chars, ASCII left-shifted 1 bit)
        Byte 6:    SSID byte (includes H-bit and end-of-address flag)

    Frame Structure:
        FLAG (0x7E) | DEST (7B) | SRC (7B) | [DIGI (7B)...] | CTRL (1B) | PID (1B) | INFO | FCS (2B) | FLAG
    """

    AX25_FLAG = 0x7E
    AX25_UI_FRAME = 0x03  # Control byte for UI frames

    @staticmethod
    def _decode_callsign(raw: bytes) -> tuple:
        """Decode 7-byte AX.25 address field → (callsign_str, ssid, has_more)."""
        if len(raw) < 7:
            return "", 0, False
        # Each character is ASCII value << 1 (left-shifted)
        callsign = ""
        for b in raw[:6]:
            char = chr(b >> 1)
            if char.strip():
                callsign += char
        ssid_byte = raw[6]
        ssid = (ssid_byte >> 1) & 0x0F
        has_more = not (ssid_byte & 0x01)  # End-of-address bit
        return callsign.strip(), ssid, has_more

    @classmethod
    def parse(cls, data: bytes) -> Optional[AX25Frame]:
        """
        Parse raw AX.25 frame bytes.

        Args:
            data: Raw bytes (with or without FLAGS and FCS)

        Returns:
            AX25Frame or None if invalid
        """
        # Strip flags
        frame_data = data
        if frame_data and frame_data[0] == cls.AX25_FLAG:
            frame_data = frame_data[1:]
        if frame_data and frame_data[-1] == cls.AX25_FLAG:
            frame_data = frame_data[:-1]
        if len(frame_data) < 17:
            return None

        frame = AX25Frame(raw=data)
        offset = 0

        # Destination address (7 bytes)
        dest_raw = frame_data[offset:offset + 7]
        dest_call, dest_ssid, _ = cls._decode_callsign(dest_raw)
        frame.destination = f"{dest_call}-{dest_ssid}" if dest_ssid else dest_call
        offset += 7

        # Source address (7 bytes)
        src_raw = frame_data[offset:offset + 7]
        src_call, src_ssid, has_more = cls._decode_callsign(src_raw)
        frame.source = f"{src_call}-{src_ssid}" if src_ssid else src_call
        offset += 7

        # Digipeater addresses (7 bytes each, while has_more)
        while has_more and offset + 7 <= len(frame_data):
            digi_raw = frame_data[offset:offset + 7]
            digi_call, digi_ssid, has_more = cls._decode_callsign(digi_raw)
            digi = f"{digi_call}-{digi_ssid}" if digi_ssid else digi_call
            frame.digipeaters.append(digi)
            offset += 7

        # Control byte
        if offset >= len(frame_data):
            return None
        control = frame_data[offset]
        offset += 1

        if control != cls.AX25_UI_FRAME:
            frame.frame_type = f"0x{control:02X}"

        # PID byte
        if offset >= len(frame_data):
            return None
        frame.pid = frame_data[offset]
        offset += 1

        # Information field (strip 2-byte FCS at end if present)
        info_end = len(frame_data)
        if info_end - offset >= 2:
            info_end -= 2  # Remove FCS
        frame.info = frame_data[offset:info_end]

        log.debug(f"AX.25: {frame.source} → {frame.destination} | PID=0x{frame.pid:02X} | {len(frame.info)}B")
        return frame

    @classmethod
    def parse_hex(cls, hex_str: str) -> Optional[AX25Frame]:
        """Parse from hex string (with or without spaces)."""
        clean = hex_str.replace(" ", "").replace(":", "")
        try:
            return cls.parse(bytes.fromhex(clean))
        except ValueError as e:
            log.error(f"Invalid hex: {e}")
            return None
