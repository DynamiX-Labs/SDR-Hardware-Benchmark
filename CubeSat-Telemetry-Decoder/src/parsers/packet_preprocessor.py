"""
Packet Preprocessor — Telemetry packet quality validation pipeline.

Inspired by YAMCS AbstractPacketPreprocessor.java and GenericPacketPreprocessor.java.
Performs timestamp extraction, sequence gap detection, CRC validation,
and packet quality scoring before packets enter the decoder pipeline.

DynamiX Labs
"""

import struct
import time
import logging
import zlib
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from .tm_packet import TmPacket, TmPacketFactory, PacketStatus

log = logging.getLogger("cubesat.preprocessor")


@dataclass
class PreprocessorStats:
    """Statistics collected by the packet preprocessor."""
    packets_processed: int = 0
    packets_valid: int = 0
    packets_invalid: int = 0
    crc_failures: int = 0
    sequence_gaps: int = 0
    duplicate_packets: int = 0
    total_missing_packets: int = 0
    avg_quality_score: float = 0.0
    _quality_sum: float = 0.0

    def update_quality(self, score: float):
        self._quality_sum += score
        if self.packets_processed > 0:
            self.avg_quality_score = self._quality_sum / self.packets_processed

    def to_dict(self) -> Dict:
        return {
            "packets_processed": self.packets_processed,
            "packets_valid": self.packets_valid,
            "packets_invalid": self.packets_invalid,
            "crc_failures": self.crc_failures,
            "sequence_gaps": self.sequence_gaps,
            "duplicate_packets": self.duplicate_packets,
            "total_missing_packets": self.total_missing_packets,
            "avg_quality_score": round(self.avg_quality_score, 4),
        }


class PacketPreprocessor:
    """
    Telemetry packet preprocessor pipeline.

    Processing stages:
      1. CRC/Checksum validation
      2. Timestamp extraction from packet header
      3. Sequence count tracking and gap detection
      4. Duplicate detection via hash ring
      5. Quality score computation
      6. Status bitfield assignment

    Usage:
        pp = PacketPreprocessor(crc_type="crc32")
        tm_packet = pp.process(raw_bytes, link="rf_frontend_0")
        if not tm_packet.is_invalid:
            decoder.decode(tm_packet)
    """

    def __init__(self,
                 crc_type: str = "crc32",
                 crc_bytes: int = 4,
                 timestamp_offset: int = -1,
                 timestamp_format: str = "cuc",
                 dedup_ring_size: int = 256):
        """
        Args:
            crc_type: Type of error detection ("crc32", "crc16", "checksum", "none")
            crc_bytes: Number of CRC bytes at end of packet
            timestamp_offset: Byte offset for timestamp in packet (-1 = auto-detect)
            timestamp_format: Timestamp format ("cuc", "cds", "unix32", "none")
            dedup_ring_size: Size of the duplicate detection hash ring
        """
        self.crc_type = crc_type
        self.crc_bytes = crc_bytes if crc_type != "none" else 0
        self.timestamp_offset = timestamp_offset
        self.timestamp_format = timestamp_format

        # Sequence tracking per APID
        self._last_sequence: Dict[int, int] = {}

        # Duplicate detection ring buffer
        self._dedup_ring: List[int] = []
        self._dedup_ring_size = dedup_ring_size

        # Statistics
        self.stats = PreprocessorStats()

        # Custom validation hooks
        self._validators: List[Callable] = []

    def add_validator(self, fn: Callable[[TmPacket], bool]):
        """Add a custom validation function. Returns True if packet is valid."""
        self._validators.append(fn)

    def process(self, raw_bytes: bytes, link: str = "",
                snr_db: float = 0.0) -> TmPacket:
        """
        Process a raw packet through the preprocessing pipeline.

        Returns a TmPacket with status flags set appropriately.
        """
        self.stats.packets_processed += 1

        # Create TmPacket
        pkt = TmPacketFactory.from_raw(raw_bytes, link=link, snr_db=snr_db)

        # Stage 1: CRC/Checksum validation
        if not self._validate_crc(pkt):
            pkt.set_invalid()
            self.stats.packets_invalid += 1
            self.stats.crc_failures += 1
            self.stats.update_quality(pkt.quality_score())
            return pkt

        # Stage 2: Timestamp extraction
        self._extract_timestamp(pkt)

        # Stage 3: Sequence tracking
        self._check_sequence(pkt)

        # Stage 4: Duplicate detection
        self._check_duplicate(pkt)

        # Stage 5: Custom validators
        for validator in self._validators:
            if not validator(pkt):
                pkt.set_invalid()
                self.stats.packets_invalid += 1
                break

        # Update stats
        if not pkt.is_invalid:
            self.stats.packets_valid += 1

        self.stats.update_quality(pkt.quality_score())

        log.debug(f"Preprocessed: {pkt}")
        return pkt

    def _validate_crc(self, pkt: TmPacket) -> bool:
        """Validate CRC/checksum on the packet."""
        if self.crc_type == "none" or len(pkt.packet) < self.crc_bytes + 1:
            return True

        data = pkt.packet[:-self.crc_bytes]
        crc_bytes = pkt.packet[-self.crc_bytes:]

        if self.crc_type == "crc32":
            computed = zlib.crc32(data) & 0xFFFFFFFF
            received = int.from_bytes(crc_bytes, 'big')
            valid = computed == received

        elif self.crc_type == "crc16":
            # CRC-CCITT
            crc = 0xFFFF
            for byte in data:
                crc ^= byte << 8
                for _ in range(8):
                    if crc & 0x8000:
                        crc = (crc << 1) ^ 0x1021
                    else:
                        crc <<= 1
                    crc &= 0xFFFF
            computed = crc
            received = int.from_bytes(crc_bytes, 'big')
            valid = computed == received

        elif self.crc_type == "checksum":
            computed = sum(data) & ((1 << (self.crc_bytes * 8)) - 1)
            received = int.from_bytes(crc_bytes, 'big')
            valid = computed == received

        else:
            valid = True

        if not valid:
            log.debug(f"CRC {self.crc_type} failed: "
                       f"computed=0x{computed:X}, received=0x{received:X}")
        return valid

    def _extract_timestamp(self, pkt: TmPacket):
        """Extract generation timestamp from packet data."""
        if self.timestamp_format == "none":
            pkt.gentime = pkt.rectime
            pkt.set_local_gen_time()
            return

        data = pkt.packet
        offset = self.timestamp_offset

        # Auto-detect: skip CCSDS primary header (6 bytes)
        if offset < 0:
            if len(data) >= 6:
                # Check if it looks like CCSDS
                version = (data[0] >> 5) & 0x07
                if version == 0:
                    offset = 6  # After primary header
                else:
                    offset = 0
            else:
                pkt.gentime = pkt.rectime
                pkt.set_local_gen_time()
                return

        try:
            if self.timestamp_format == "cuc" and offset + 5 <= len(data):
                # CUC: P-field + coarse time (4 bytes)
                coarse = struct.unpack(">I", data[offset + 1:offset + 5])[0]
                pkt.gentime = float(coarse)
                # CCSDS epoch is 1958-01-01, convert to Unix
                pkt.gentime += -378691200  # Approximate offset

            elif self.timestamp_format == "cds" and offset + 6 <= len(data):
                # CDS: days (2 bytes) + ms_of_day (4 bytes)
                days, ms_of_day = struct.unpack(">HI", data[offset:offset + 6])
                # CCSDS epoch
                pkt.gentime = days * 86400.0 + ms_of_day / 1000.0 - 378691200

            elif self.timestamp_format == "unix32" and offset + 4 <= len(data):
                pkt.gentime = float(struct.unpack(">I", data[offset:offset + 4])[0])

            else:
                pkt.gentime = pkt.rectime
                pkt.set_local_gen_time()

        except (struct.error, ValueError):
            pkt.gentime = pkt.rectime
            pkt.set_local_gen_time()

    def _check_sequence(self, pkt: TmPacket):
        """Check sequence count continuity per APID."""
        if pkt.apid < 0:
            return

        apid = pkt.apid
        seq = pkt.seq_count

        if apid in self._last_sequence:
            expected = (self._last_sequence[apid] + 1) & 0x3FFF
            if seq != expected:
                gap = (seq - expected) & 0x3FFF
                self.stats.sequence_gaps += 1
                self.stats.total_missing_packets += gap
                log.warning(
                    f"Sequence gap APID={apid}: expected {expected}, got {seq} "
                    f"({gap} missing)"
                )

        self._last_sequence[apid] = seq

    def _check_duplicate(self, pkt: TmPacket):
        """Detect duplicate packets using a hash ring."""
        pkt_hash = hash(pkt.packet)

        if pkt_hash in self._dedup_ring:
            pkt.set_duplicate()
            self.stats.duplicate_packets += 1
            log.debug(f"Duplicate packet detected (hash={pkt_hash})")
        else:
            self._dedup_ring.append(pkt_hash)
            if len(self._dedup_ring) > self._dedup_ring_size:
                self._dedup_ring.pop(0)
