"""
HDLC Deframer
Extracts HDLC frames from a bitstream with flag detection, bit-unstuffing,
and CRC-CCITT verification.

Ported from gr-satellites hdlc_deframer.py.
DynamiX Labs
"""

import struct
import logging
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("cubesat.hdlc")

# CRC-CCITT lookup table (polynomial 0x8408, reflected)
_CRC_TABLE = []
for _byte in range(256):
    _crc = _byte
    for _ in range(8):
        if _crc & 1:
            _crc = (_crc >> 1) ^ 0x8408
        else:
            _crc >>= 1
    _CRC_TABLE.append(_crc)


def crc_ccitt(data: bytes) -> int:
    """Compute CRC-CCITT (X.25) over data bytes."""
    crc = 0xFFFF
    for byte in data:
        crc = (_CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)) & 0xFFFF
    return crc ^ 0xFFFF


@dataclass
class HDLCFrame:
    """Parsed HDLC frame."""
    address: int = 0xFF
    control: int = 0x03
    payload: bytes = b""
    fcs: int = 0
    fcs_valid: bool = False
    raw: bytes = b""


class HDLCDeframer:
    """
    HDLC frame deframer.

    Operates on a bitstream to:
      1. Detect 0x7E flag sequences
      2. Perform bit-unstuffing (remove stuffed zeros after five ones)
      3. Extract frames between flags
      4. Verify CRC-CCITT (FCS)

    HDLC Frame Structure:
        FLAG (0x7E) | ADDRESS (1B) | CONTROL (1B) | INFO (N bytes) | FCS (2B) | FLAG (0x7E)
    """

    FLAG = 0x7E
    FLAG_BITS = [0, 1, 1, 1, 1, 1, 1, 0]  # 0x7E in bit order
    MIN_FRAME_BYTES = 4  # Address + Control + 2-byte FCS minimum

    def __init__(self):
        self._buffer = []
        self._in_frame = False
        self._ones_count = 0
        self._frames_decoded = 0
        self._frames_failed_crc = 0

    def _bits_to_bytes(self, bits: List[int]) -> bytes:
        """Convert a list of bits to bytes (LSB first within each byte)."""
        result = bytearray()
        for i in range(0, len(bits) - 7, 8):
            byte = 0
            for j in range(8):
                byte |= (bits[i + j] << j)
            result.append(byte)
        return bytes(result)

    def _unstuff(self, bits: List[int]) -> List[int]:
        """Remove bit-stuffing: after five consecutive 1s, remove the next 0."""
        output = []
        ones = 0

        for bit in bits:
            if ones == 5:
                if bit == 0:
                    # Stuffed bit — skip it
                    ones = 0
                    continue
                else:
                    # Six ones in a row — invalid (flag or abort)
                    ones = 0
                    continue  # Could also signal abort
            output.append(bit)
            if bit == 1:
                ones += 1
            else:
                ones = 0

        return output

    def process_bits(self, bitstream: List[int]) -> List[HDLCFrame]:
        """
        Process a bitstream and extract HDLC frames.

        Args:
            bitstream: List of 0s and 1s

        Returns:
            List of extracted HDLCFrame objects
        """
        frames = []
        i = 0

        while i < len(bitstream) - 7:
            # Check for flag pattern (0x7E = 01111110)
            if bitstream[i:i + 8] == self.FLAG_BITS:
                if self._in_frame and len(self._buffer) > 0:
                    # End of frame — process buffered bits
                    frame = self._process_frame_bits(self._buffer)
                    if frame is not None:
                        frames.append(frame)
                # Start new frame
                self._buffer = []
                self._in_frame = True
                self._ones_count = 0
                i += 8
                continue

            if self._in_frame:
                self._buffer.append(bitstream[i])

            i += 1

        return frames

    def process_bytes(self, data: bytes) -> List[HDLCFrame]:
        """Process a byte stream by converting to bits first."""
        import numpy as np
        bits = list(np.unpackbits(np.frombuffer(data, dtype=np.uint8)))
        return self.process_bits(bits)

    def deframe(self, data: bytes) -> List[HDLCFrame]:
        """
        Extract HDLC frames from raw byte data.

        Simpler approach: scan for 0x7E flags directly in byte stream.
        Works when data is byte-aligned (common for AX.25 KISS frames).
        """
        frames = []
        i = 0

        while i < len(data):
            # Find start flag
            start = data.find(bytes([self.FLAG]), i)
            if start == -1:
                break

            # Find end flag
            end = data.find(bytes([self.FLAG]), start + 1)
            if end == -1:
                break

            # Skip consecutive flags
            while end < len(data) - 1 and data[end + 1] == self.FLAG:
                end += 1

            frame_data = data[start + 1:end]

            if len(frame_data) >= self.MIN_FRAME_BYTES:
                frame = self._parse_frame_bytes(frame_data)
                if frame is not None:
                    frames.append(frame)

            i = end + 1

        return frames

    def _process_frame_bits(self, bits: List[int]) -> Optional[HDLCFrame]:
        """Process accumulated frame bits into an HDLCFrame."""
        # Unstuff
        unstuffed = self._unstuff(bits)

        # Need at least MIN_FRAME_BYTES * 8 bits
        if len(unstuffed) < self.MIN_FRAME_BYTES * 8:
            return None

        # Convert to bytes
        frame_bytes = self._bits_to_bytes(unstuffed)
        return self._parse_frame_bytes(frame_bytes)

    def _parse_frame_bytes(self, frame_bytes: bytes) -> Optional[HDLCFrame]:
        """Parse frame bytes into an HDLCFrame with CRC verification."""
        if len(frame_bytes) < self.MIN_FRAME_BYTES:
            return None

        frame = HDLCFrame(raw=frame_bytes)

        # Extract FCS (last 2 bytes, little-endian)
        fcs_received = struct.unpack("<H", frame_bytes[-2:])[0]
        frame.fcs = fcs_received

        # Verify CRC over all bytes except FCS
        content = frame_bytes[:-2]
        computed_crc = crc_ccitt(content)
        frame.fcs_valid = (computed_crc == fcs_received)

        if not frame.fcs_valid:
            self._frames_failed_crc += 1
            log.debug(f"HDLC CRC mismatch: computed=0x{computed_crc:04X}, "
                       f"received=0x{fcs_received:04X}")
        else:
            self._frames_decoded += 1

        # Parse address and control
        if len(content) >= 2:
            frame.address = content[0]
            frame.control = content[1]
            frame.payload = content[2:]

        return frame

    @property
    def stats(self) -> dict:
        """Return deframer statistics."""
        total = self._frames_decoded + self._frames_failed_crc
        return {
            "frames_decoded": self._frames_decoded,
            "frames_failed_crc": self._frames_failed_crc,
            "total_frames": total,
            "success_rate": (self._frames_decoded / total * 100) if total > 0 else 0.0,
        }
