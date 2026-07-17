"""
CCSDS Space Packet Parser
Full implementation of CCSDS 133.0-B-1 Space Packet Protocol.

Ported from gr-satellites space_packet.py with Python-native struct parsing.
DynamiX Labs
"""

import struct
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import IntEnum

log = logging.getLogger("cubesat.ccsds")


class PacketType(IntEnum):
    TELEMETRY = 0
    TELECOMMAND = 1


class SequenceFlags(IntEnum):
    CONTINUATION = 0b00
    FIRST = 0b01
    LAST = 0b10
    STANDALONE = 0b11


@dataclass
class CCSDSPrimaryHeader:
    """CCSDS Space Packet Primary Header (6 bytes / 48 bits)."""
    version: int = 0           # 3 bits — always 000 for current version
    packet_type: int = 0       # 1 bit — 0=TM, 1=TC
    secondary_header_flag: bool = False  # 1 bit
    apid: int = 0              # 11 bits — Application Process Identifier
    sequence_flags: int = 3    # 2 bits — segmentation flags
    sequence_count: int = 0    # 14 bits — packet sequence counter
    data_length: int = 0       # 16 bits — (num data bytes - 1)

    @property
    def is_telemetry(self) -> bool:
        return self.packet_type == PacketType.TELEMETRY

    @property
    def is_telecommand(self) -> bool:
        return self.packet_type == PacketType.TELECOMMAND

    @property
    def is_standalone(self) -> bool:
        return self.sequence_flags == SequenceFlags.STANDALONE

    @property
    def total_packet_length(self) -> int:
        """Total packet length = primary header (6) + data field (data_length + 1)."""
        return 6 + self.data_length + 1


@dataclass
class CCSDSSpacePacket:
    """Complete CCSDS Space Packet with parsed fields."""
    header: CCSDSPrimaryHeader = field(default_factory=CCSDSPrimaryHeader)
    secondary_header: Optional[bytes] = None
    payload: bytes = b""
    raw: bytes = b""
    timestamp: Optional[float] = None  # Extracted from secondary header if present


class CCSDSPacketParser:
    """
    Parses CCSDS Space Packets per CCSDS 133.0-B-1.

    Packet structure:
        [Primary Header: 6 bytes][Secondary Header: variable][User Data][Packet Error Control: optional]

    Primary Header (6 bytes = 48 bits):
        Bits 0-2:   Version Number (3 bits)
        Bit  3:     Packet Type (0=TM, 1=TC)
        Bit  4:     Secondary Header Flag
        Bits 5-15:  APID (11 bits)
        Bits 16-17: Sequence Flags (2 bits)
        Bits 18-31: Sequence Count (14 bits)
        Bits 32-47: Data Length (16 bits) — (num octets in data field - 1)
    """

    PRIMARY_HEADER_SIZE = 6

    @classmethod
    def parse_primary_header(cls, data: bytes) -> Optional[CCSDSPrimaryHeader]:
        """Parse the 6-byte primary header."""
        if len(data) < cls.PRIMARY_HEADER_SIZE:
            log.warning(f"Data too short for CCSDS primary header: {len(data)} bytes")
            return None

        word0, word1, word2 = struct.unpack(">HHH", data[:6])

        header = CCSDSPrimaryHeader(
            version=(word0 >> 13) & 0x07,
            packet_type=(word0 >> 12) & 0x01,
            secondary_header_flag=bool((word0 >> 11) & 0x01),
            apid=word0 & 0x07FF,
            sequence_flags=(word1 >> 14) & 0x03,
            sequence_count=word1 & 0x3FFF,
            data_length=word2,
        )

        # Validate version
        if header.version != 0:
            log.warning(f"Unexpected CCSDS version: {header.version}")

        return header

    @classmethod
    def parse(cls, data: bytes) -> Optional[CCSDSSpacePacket]:
        """Parse a complete CCSDS Space Packet from raw bytes."""
        header = cls.parse_primary_header(data)
        if header is None:
            return None

        packet = CCSDSSpacePacket(header=header, raw=data)

        # Extract data field
        data_field_length = header.data_length + 1
        data_field = data[cls.PRIMARY_HEADER_SIZE:
                          cls.PRIMARY_HEADER_SIZE + data_field_length]

        if len(data_field) < data_field_length:
            log.warning(f"Truncated packet: expected {data_field_length} data bytes, "
                        f"got {len(data_field)}")
            data_field_length = len(data_field)

        # Parse secondary header if present
        offset = 0
        if header.secondary_header_flag and len(data_field) >= 4:
            # CUC time code: typically 4-7 bytes
            # Try to detect P-field
            pfield = data_field[0]
            time_code_id = (pfield >> 4) & 0x07
            n_basic = ((pfield >> 2) & 0x03) + 1
            n_frac = pfield & 0x03

            sec_header_len = 1 + n_basic + n_frac  # P-field + time units
            if sec_header_len <= len(data_field):
                packet.secondary_header = data_field[:sec_header_len]
                offset = sec_header_len

                # Extract timestamp from CUC time code
                if n_basic > 0:
                    basic_bytes = data_field[1:1 + n_basic]
                    timestamp_val = int.from_bytes(basic_bytes, 'big')
                    if n_frac > 0:
                        frac_bytes = data_field[1 + n_basic:1 + n_basic + n_frac]
                        frac_val = int.from_bytes(frac_bytes, 'big')
                        timestamp_val += frac_val / (256 ** n_frac)
                    packet.timestamp = float(timestamp_val)

        packet.payload = data_field[offset:]

        log.debug(
            f"CCSDS: APID={header.apid} Seq={header.sequence_count} "
            f"Type={'TM' if header.is_telemetry else 'TC'} "
            f"Len={data_field_length}B"
        )
        return packet

    @classmethod
    def parse_stream(cls, stream: bytes,
                     max_packets: int = 1000) -> List[CCSDSSpacePacket]:
        """Parse multiple CCSDS packets from a byte stream."""
        packets = []
        offset = 0

        while offset < len(stream) - cls.PRIMARY_HEADER_SIZE and len(packets) < max_packets:
            header = cls.parse_primary_header(stream[offset:])
            if header is None:
                offset += 1
                continue

            pkt_len = header.total_packet_length
            if offset + pkt_len > len(stream):
                break

            pkt = cls.parse(stream[offset:offset + pkt_len])
            if pkt:
                packets.append(pkt)
            offset += pkt_len

        return packets


class VirtualChannelDemux:
    """
    CCSDS Virtual Channel demultiplexer.

    Inspired by gr-satellites virtual_channel_demultiplexer.py.
    Routes packets to handlers based on APID ranges.
    """

    def __init__(self):
        self._handlers: Dict[int, list] = {}
        self._default_handler = None

    def register(self, apid: int, handler):
        """Register a handler for a specific APID."""
        if apid not in self._handlers:
            self._handlers[apid] = []
        self._handlers[apid].append(handler)

    def register_range(self, apid_start: int, apid_end: int, handler):
        """Register a handler for an APID range."""
        for apid in range(apid_start, apid_end + 1):
            self.register(apid, handler)

    def set_default(self, handler):
        """Set default handler for unregistered APIDs."""
        self._default_handler = handler

    def route(self, packet: CCSDSSpacePacket):
        """Route a packet to registered handlers."""
        apid = packet.header.apid
        handlers = self._handlers.get(apid, [])

        if handlers:
            for h in handlers:
                h(packet)
        elif self._default_handler:
            self._default_handler(packet)
        else:
            log.debug(f"No handler for APID {apid}")


class SequenceTracker:
    """
    Tracks CCSDS packet sequence counts per APID for gap detection.

    Inspired by YAMCS CcsdsSeqCountFiller.java.
    """

    def __init__(self):
        self._last_seq: Dict[int, int] = {}
        self._gap_count: Dict[int, int] = {}

    def check(self, packet: CCSDSSpacePacket) -> Dict:
        """Check sequence continuity for a packet. Returns gap info."""
        apid = packet.header.apid
        seq = packet.header.sequence_count

        result = {
            "apid": apid,
            "sequence_count": seq,
            "gap_detected": False,
            "missing_packets": 0,
        }

        if apid in self._last_seq:
            expected = (self._last_seq[apid] + 1) & 0x3FFF  # 14-bit wrap
            if seq != expected:
                gap = (seq - expected) & 0x3FFF
                result["gap_detected"] = True
                result["missing_packets"] = gap
                self._gap_count[apid] = self._gap_count.get(apid, 0) + gap
                log.warning(
                    f"Sequence gap on APID {apid}: expected {expected}, "
                    f"got {seq} ({gap} packets missing)"
                )

        self._last_seq[apid] = seq
        return result

    def get_stats(self) -> Dict:
        """Return sequence tracking statistics."""
        return {
            "tracked_apids": len(self._last_seq),
            "total_gaps": sum(self._gap_count.values()),
            "per_apid_gaps": dict(self._gap_count),
        }
