"""
TmPacket — Telemetry Packet Model

Ported from YAMCS TmPacket.java. Provides a unified telemetry packet
container with generation/reception timestamps, sequence tracking,
status bitfield, and link association.

DynamiX Labs
"""

import time
import struct
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import IntFlag

log = logging.getLogger("cubesat.tmpacket")


class PacketStatus(IntFlag):
    """
    32-bit packet status bitfield (YAMCS compatible).

    Bit layout:
      bit 0 (LSB): INVALID — packet failed CRC/checksum verification
      bit 1:       LOCAL_GEN_TIME — generation time is local fallback
      bit 2:       DO_NOT_ARCHIVE — packet should not be persisted
      bits 3-15:   USER_DEFINED — preprocessor-specific flags
      bits 16-28:  RESERVED — for future use
      bit 29:      FEC_CORRECTED — errors were corrected by FEC
      bit 30:      DUPLICATE — packet is a detected duplicate
      bit 31 (MSB): always 0
    """
    NONE = 0
    INVALID = 1 << 0
    LOCAL_GEN_TIME = 1 << 1
    DO_NOT_ARCHIVE = 1 << 2
    FEC_CORRECTED = 1 << 29
    DUPLICATE = 1 << 30


@dataclass
class TmPacket:
    """
    Telemetry packet container with YAMCS-compatible metadata.

    Fields:
      packet:    Raw packet bytes
      rectime:   Reception time (Unix timestamp, when received by ground station)
      gentime:   Generation time (Unix timestamp, when generated onboard)
      ertime:    Earth reception time (high-precision, frame-level timestamp)
      seq_count: Sequence count (APID + sequence counter combo for uniqueness)
      status:    32-bit status bitfield (PacketStatus flags)
      link:      Name of the data link on which packet was received
      apid:      Application Process Identifier (from CCSDS header)
      obt:       On-Board Time (free-running clock value)
    """
    packet: bytes = b""
    rectime: float = 0.0          # Unix timestamp
    gentime: float = 0.0          # Unix timestamp
    ertime: float = 0.0           # High-resolution earth reception time
    seq_count: int = 0
    status: int = PacketStatus.NONE
    link: str = ""
    apid: int = -1
    obt: int = -1
    frame_seq_count: int = -1
    root_container: str = ""

    # Quality metadata
    snr_db: float = 0.0           # SNR at time of reception
    ber: float = 0.0              # Bit error rate estimate
    fec_corrections: int = 0      # Number of FEC-corrected errors

    @property
    def is_invalid(self) -> bool:
        return bool(self.status & PacketStatus.INVALID)

    @property
    def is_local_gen_time(self) -> bool:
        return bool(self.status & PacketStatus.LOCAL_GEN_TIME)

    @property
    def do_not_archive(self) -> bool:
        return bool(self.status & PacketStatus.DO_NOT_ARCHIVE)

    @property
    def is_fec_corrected(self) -> bool:
        return bool(self.status & PacketStatus.FEC_CORRECTED)

    @property
    def is_duplicate(self) -> bool:
        return bool(self.status & PacketStatus.DUPLICATE)

    @property
    def length(self) -> int:
        return len(self.packet)

    def set_invalid(self, invalid: bool = True):
        if invalid:
            self.status |= PacketStatus.INVALID
        else:
            self.status &= ~PacketStatus.INVALID

    def set_local_gen_time(self):
        self.status |= PacketStatus.LOCAL_GEN_TIME

    def set_do_not_archive(self):
        self.status |= PacketStatus.DO_NOT_ARCHIVE

    def set_fec_corrected(self, corrections: int = 0):
        self.status |= PacketStatus.FEC_CORRECTED
        self.fec_corrections = corrections

    def set_duplicate(self):
        self.status |= PacketStatus.DUPLICATE

    def quality_score(self) -> float:
        """
        Compute a quality score [0.0, 1.0] based on packet metadata.
        1.0 = perfect packet, 0.0 = completely unreliable.
        """
        score = 1.0
        if self.is_invalid:
            score *= 0.0
        if self.is_fec_corrected:
            score *= max(0.5, 1.0 - self.fec_corrections * 0.05)
        if self.is_local_gen_time:
            score *= 0.9  # Slight penalty for uncertain timing
        if self.is_duplicate:
            score *= 0.8
        if self.snr_db > 0:
            # Higher SNR = better quality
            score *= min(1.0, self.snr_db / 20.0)
        return max(0.0, min(1.0, score))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for JSON/database storage."""
        return {
            "rectime": self.rectime,
            "gentime": self.gentime,
            "ertime": self.ertime,
            "seq_count": self.seq_count,
            "apid": self.apid,
            "length": self.length,
            "status": self.status,
            "status_flags": {
                "invalid": self.is_invalid,
                "local_gen_time": self.is_local_gen_time,
                "do_not_archive": self.do_not_archive,
                "fec_corrected": self.is_fec_corrected,
                "duplicate": self.is_duplicate,
            },
            "link": self.link,
            "snr_db": self.snr_db,
            "ber": self.ber,
            "fec_corrections": self.fec_corrections,
            "quality_score": round(self.quality_score(), 4),
            "payload_hex": self.packet.hex(),
        }

    def __repr__(self) -> str:
        flags = []
        if self.is_invalid:
            flags.append("INVALID")
        if self.is_fec_corrected:
            flags.append("FEC")
        if self.is_duplicate:
            flags.append("DUP")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        return (
            f"TmPacket(APID={self.apid} Seq={self.seq_count} "
            f"Len={self.length}B Q={self.quality_score():.2f}{flag_str})"
        )


class TmPacketFactory:
    """
    Factory for creating TmPackets from raw data.
    Inspired by YAMCS packet preprocessing pipeline.
    """

    @staticmethod
    def from_raw(raw_bytes: bytes, link: str = "",
                 snr_db: float = 0.0) -> TmPacket:
        """Create a TmPacket from raw bytes with current reception time."""
        pkt = TmPacket(
            packet=raw_bytes,
            rectime=time.time(),
            link=link,
            snr_db=snr_db,
        )
        # Try to extract APID and sequence count from CCSDS primary header
        if len(raw_bytes) >= 6:
            word0, word1 = struct.unpack(">HH", raw_bytes[:4])
            version = (word0 >> 13) & 0x07
            if version == 0:  # Valid CCSDS packet
                pkt.apid = word0 & 0x07FF
                pkt.seq_count = word1 & 0x3FFF
        return pkt

    @staticmethod
    def from_ccsds(ccsds_packet, link: str = "",
                   snr_db: float = 0.0) -> TmPacket:
        """Create a TmPacket from a parsed CCSDSSpacePacket."""
        pkt = TmPacket(
            packet=ccsds_packet.raw,
            rectime=time.time(),
            link=link,
            snr_db=snr_db,
            apid=ccsds_packet.header.apid,
            seq_count=ccsds_packet.header.sequence_count,
        )
        if ccsds_packet.timestamp is not None:
            pkt.gentime = ccsds_packet.timestamp
        else:
            pkt.gentime = pkt.rectime
            pkt.set_local_gen_time()
        return pkt
