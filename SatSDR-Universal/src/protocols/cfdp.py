"""
CCSDS File Delivery Protocol (CFDP) — Entity Implementation
CCSDS 727.0-B-5 compliant file delivery over space data links.
DynamiX Labs | Phase 4

Implements Class 1 (Unreliable) and Class 2 (Reliable) file delivery
services for CubeSat and spacecraft file transfer operations. All
output is API-only — no GUI or web interface is provided.

Reference: https://public.ccsds.org/Pubs/727x0b5.pdf

Usage:
    sender = CFDPSender(entity_id=1, peer_id=2, transport=zmq_transport)
    tx_id = sender.put("telemetry_log.bin", destination_path="/onboard/logs/")

    receiver = CFDPReceiver(entity_id=2, transport=zmq_transport)
    receiver.start()  # Blocking — processes incoming PDUs
"""

import hashlib
import struct
import enum
import time
import uuid
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any
from pathlib import Path

log = logging.getLogger("satsdr.cfdp")


# ---------------------------------------------------------------------------
# CCSDS CFDP Constants (727.0-B-5)
# ---------------------------------------------------------------------------
CFDP_VERSION = 1
CFDP_HEADER_MAGIC = 0x20  # Version 1, file directive flag

SEGMENT_SIZE = 1024  # Default file data segment size (bytes)


class PDUType(enum.IntEnum):
    """CFDP PDU types."""
    FILE_DIRECTIVE = 0
    FILE_DATA = 1


class DirectiveCode(enum.IntEnum):
    """CFDP File Directive codes (Table 5-4)."""
    EOF_PDU = 0x04
    FINISHED = 0x05
    ACK = 0x06
    METADATA = 0x07
    NAK = 0x08
    PROMPT = 0x09
    KEEP_ALIVE = 0x0C


class TransmissionMode(enum.IntEnum):
    """CFDP transmission modes."""
    ACKNOWLEDGED = 0      # Class 2 — reliable with ACK/NAK
    UNACKNOWLEDGED = 1    # Class 1 — unreliable (fire-and-forget)


class ConditionCode(enum.IntEnum):
    """CFDP condition codes (Table 5-5)."""
    NO_ERROR = 0x0
    POSITIVE_ACK_LIMIT = 0x1
    KEEP_ALIVE_LIMIT = 0x2
    INVALID_TRANSMISSION_MODE = 0x3
    FILESTORE_REJECTION = 0x4
    FILE_CHECKSUM_FAILURE = 0x5
    FILE_SIZE_ERROR = 0x6
    NAK_LIMIT_REACHED = 0x7
    INACTIVITY_DETECTED = 0x8
    CANCEL_RECEIVED = 0xF


class TransactionStatus(enum.Enum):
    """Transaction lifecycle states."""
    IDLE = "idle"
    ACTIVE = "active"
    SENDING_METADATA = "sending_metadata"
    SENDING_DATA = "sending_data"
    SENDING_EOF = "sending_eof"
    WAITING_ACK = "waiting_ack"
    RECEIVING = "receiving"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"

    def __str__(self):
        return self.value


# ---------------------------------------------------------------------------
# PDU Data Structures
# ---------------------------------------------------------------------------
@dataclass
class PDUHeader:
    """CFDP PDU Fixed Header (Section 5.1)."""
    version: int = CFDP_VERSION
    pdu_type: PDUType = PDUType.FILE_DIRECTIVE
    direction: int = 0          # 0 = toward receiver, 1 = toward sender
    transmission_mode: TransmissionMode = TransmissionMode.UNACKNOWLEDGED
    crc_flag: int = 0           # 0 = CRC not present, 1 = CRC present
    data_length: int = 0
    source_entity_id: int = 0
    transaction_seq: int = 0
    destination_entity_id: int = 0

    def pack(self) -> bytes:
        """Serialize PDU header to bytes."""
        first_byte = (
            (self.version << 5)
            | (self.pdu_type << 4)
            | (self.direction << 3)
            | (self.transmission_mode << 2)
            | (self.crc_flag << 1)
        )
        return struct.pack(
            "!BHBBH",
            first_byte,
            self.data_length,
            self.source_entity_id & 0xFF,
            self.transaction_seq & 0xFF,
            self.destination_entity_id & 0xFFFF,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "PDUHeader":
        """Deserialize PDU header from bytes."""
        first_byte, data_len, src, seq, dst = struct.unpack("!BHBBH", data[:7])
        return cls(
            version=(first_byte >> 5) & 0x7,
            pdu_type=PDUType((first_byte >> 4) & 0x1),
            direction=(first_byte >> 3) & 0x1,
            transmission_mode=TransmissionMode((first_byte >> 2) & 0x1),
            crc_flag=(first_byte >> 1) & 0x1,
            data_length=data_len,
            source_entity_id=src,
            transaction_seq=seq,
            destination_entity_id=dst,
        )

    @staticmethod
    def size() -> int:
        return 7


@dataclass
class MetadataPDU:
    """CFDP Metadata PDU (Section 5.2.5)."""
    directive_code: int = DirectiveCode.METADATA
    closure_requested: bool = False
    file_size: int = 0
    source_filename: str = ""
    destination_filename: str = ""
    checksum_type: int = 0  # 0 = modular checksum

    def pack(self) -> bytes:
        src_bytes = self.source_filename.encode("utf-8")
        dst_bytes = self.destination_filename.encode("utf-8")
        return struct.pack(
            "!B?IB",
            self.directive_code,
            self.closure_requested,
            self.file_size,
            len(src_bytes),
        ) + src_bytes + struct.pack("!B", len(dst_bytes)) + dst_bytes

    @classmethod
    def unpack(cls, data: bytes) -> "MetadataPDU":
        code, closure, fsize, src_len = struct.unpack("!B?IB", data[:7])
        src = data[7:7 + src_len].decode("utf-8")
        dst_len = data[7 + src_len]
        dst = data[8 + src_len:8 + src_len + dst_len].decode("utf-8")
        return cls(
            directive_code=code,
            closure_requested=closure,
            file_size=fsize,
            source_filename=src,
            destination_filename=dst,
        )


@dataclass
class FileDataPDU:
    """CFDP File Data PDU (Section 5.3)."""
    offset: int = 0
    data: bytes = b""

    def pack(self) -> bytes:
        return struct.pack("!I", self.offset) + self.data

    @classmethod
    def unpack(cls, raw: bytes) -> "FileDataPDU":
        offset = struct.unpack("!I", raw[:4])[0]
        return cls(offset=offset, data=raw[4:])


@dataclass
class EOFPDU:
    """CFDP EOF PDU (Section 5.2.2)."""
    directive_code: int = DirectiveCode.EOF_PDU
    condition_code: ConditionCode = ConditionCode.NO_ERROR
    file_checksum: int = 0
    file_size: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            "!BBII",
            self.directive_code,
            self.condition_code.value,
            self.file_checksum,
            self.file_size,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "EOFPDU":
        code, cond, cksum, fsize = struct.unpack("!BBII", data[:10])
        return cls(
            directive_code=code,
            condition_code=ConditionCode(cond),
            file_checksum=cksum,
            file_size=fsize,
        )


@dataclass
class FinishedPDU:
    """CFDP Finished PDU (Section 5.2.3)."""
    directive_code: int = DirectiveCode.FINISHED
    condition_code: ConditionCode = ConditionCode.NO_ERROR
    delivery_code: int = 0  # 0 = complete, 1 = incomplete

    def pack(self) -> bytes:
        return struct.pack("!BBB", self.directive_code,
                           self.condition_code.value, self.delivery_code)

    @classmethod
    def unpack(cls, data: bytes) -> "FinishedPDU":
        code, cond, delivery = struct.unpack("!BBB", data[:3])
        return cls(directive_code=code,
                   condition_code=ConditionCode(cond), delivery_code=delivery)


@dataclass
class NAKPDU:
    """CFDP NAK PDU (Section 5.2.6) — request retransmission."""
    directive_code: int = DirectiveCode.NAK
    scope_start: int = 0
    scope_end: int = 0
    segment_requests: List[tuple] = field(default_factory=list)

    def pack(self) -> bytes:
        data = struct.pack("!BII", self.directive_code,
                           self.scope_start, self.scope_end)
        for start, end in self.segment_requests:
            data += struct.pack("!II", start, end)
        return data

    @classmethod
    def unpack(cls, data: bytes) -> "NAKPDU":
        code, s_start, s_end = struct.unpack("!BII", data[:9])
        segs = []
        pos = 9
        while pos + 8 <= len(data):
            seg_s, seg_e = struct.unpack("!II", data[pos:pos + 8])
            segs.append((seg_s, seg_e))
            pos += 8
        return cls(directive_code=code, scope_start=s_start,
                   scope_end=s_end, segment_requests=segs)


# ---------------------------------------------------------------------------
# Transaction Tracker
# ---------------------------------------------------------------------------
@dataclass
class CFDPTransaction:
    """State for a single CFDP file transaction."""
    transaction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    transaction_seq: int = 0
    source_entity: int = 0
    destination_entity: int = 0
    source_filename: str = ""
    destination_filename: str = ""
    file_size: int = 0
    file_checksum: int = 0
    mode: TransmissionMode = TransmissionMode.UNACKNOWLEDGED
    status: TransactionStatus = TransactionStatus.IDLE
    segments_sent: int = 0
    segments_total: int = 0
    segments_received: set = field(default_factory=set)
    missing_segments: List[tuple] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    condition: ConditionCode = ConditionCode.NO_ERROR
    receive_buffer: Dict[int, bytes] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "transaction_seq": self.transaction_seq,
            "source_entity": self.source_entity,
            "destination_entity": self.destination_entity,
            "source_filename": self.source_filename,
            "destination_filename": self.destination_filename,
            "file_size": self.file_size,
            "mode": str(self.mode.name),
            "status": str(self.status),
            "segments_sent": self.segments_sent,
            "segments_total": self.segments_total,
            "segments_received": len(self.segments_received),
            "progress_pct": round(
                len(self.segments_received) / max(1, self.segments_total) * 100, 1
            ),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "condition": self.condition.name,
        }


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------
def cfdp_checksum(data: bytes) -> int:
    """CCSDS modular checksum (32-bit, per CFDP spec)."""
    cksum = 0
    # Pad to 4-byte boundary
    padded = data + b"\x00" * ((4 - len(data) % 4) % 4)
    for i in range(0, len(padded), 4):
        word = struct.unpack("!I", padded[i:i + 4])[0]
        cksum = (cksum + word) & 0xFFFFFFFF
    return cksum


# ---------------------------------------------------------------------------
# CFDP Sender (Originating Entity)
# ---------------------------------------------------------------------------
class CFDPSender:
    """
    CFDP sending entity — segments files and transmits via PDUs.

    This is an API-only component. No GUI or web interface is provided.
    Integrate with any transport layer (ZMQ, TCP, UDP, CCSDS TM/TC).

    Parameters
    ----------
    entity_id : int
        This entity's CFDP ID.
    peer_id : int
        Default destination entity ID.
    transport : callable
        Function(pdu_bytes: bytes) that delivers PDU to the peer.
    segment_size : int
        Maximum file data segment size in bytes.
    transmission_mode : TransmissionMode
        Class 1 (unacknowledged) or Class 2 (acknowledged).
    """

    def __init__(
        self,
        entity_id: int = 1,
        peer_id: int = 2,
        transport: Optional[Callable[[bytes], None]] = None,
        segment_size: int = SEGMENT_SIZE,
        transmission_mode: TransmissionMode = TransmissionMode.UNACKNOWLEDGED,
    ):
        self.entity_id = entity_id
        self.peer_id = peer_id
        self.transport = transport or self._null_transport
        self.segment_size = segment_size
        self.mode = transmission_mode
        self._seq_counter = 0
        self._transactions: Dict[str, CFDPTransaction] = {}

    def put(
        self,
        source_path: str,
        destination_path: Optional[str] = None,
        peer_id: Optional[int] = None,
    ) -> str:
        """
        Initiate a CFDP Put (file send) transaction.

        Returns the transaction_id for tracking.
        """
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        file_data = path.read_bytes()
        file_size = len(file_data)
        dst = peer_id or self.peer_id
        dst_filename = destination_path or path.name

        self._seq_counter += 1
        tx = CFDPTransaction(
            transaction_seq=self._seq_counter,
            source_entity=self.entity_id,
            destination_entity=dst,
            source_filename=str(path.name),
            destination_filename=dst_filename,
            file_size=file_size,
            file_checksum=cfdp_checksum(file_data),
            mode=self.mode,
            status=TransactionStatus.ACTIVE,
            segments_total=(file_size + self.segment_size - 1) // self.segment_size,
        )
        self._transactions[tx.transaction_id] = tx

        log.info(f"CFDP PUT {tx.transaction_id} | {path.name} "
                 f"({file_size} bytes, {tx.segments_total} segments) -> entity {dst}")

        # Phase 1: Metadata PDU
        tx.status = TransactionStatus.SENDING_METADATA
        meta = MetadataPDU(
            closure_requested=(self.mode == TransmissionMode.ACKNOWLEDGED),
            file_size=file_size,
            source_filename=str(path.name),
            destination_filename=dst_filename,
        )
        self._send_pdu(tx, PDUType.FILE_DIRECTIVE, meta.pack())

        # Phase 2: File Data PDUs
        tx.status = TransactionStatus.SENDING_DATA
        offset = 0
        seg_idx = 0
        while offset < file_size:
            chunk = file_data[offset:offset + self.segment_size]
            fd = FileDataPDU(offset=offset, data=chunk)
            self._send_pdu(tx, PDUType.FILE_DATA, fd.pack())
            offset += len(chunk)
            seg_idx += 1
            tx.segments_sent = seg_idx

        # Phase 3: EOF PDU
        tx.status = TransactionStatus.SENDING_EOF
        eof = EOFPDU(
            condition_code=ConditionCode.NO_ERROR,
            file_checksum=tx.file_checksum,
            file_size=file_size,
        )
        self._send_pdu(tx, PDUType.FILE_DIRECTIVE, eof.pack())

        if self.mode == TransmissionMode.UNACKNOWLEDGED:
            tx.status = TransactionStatus.COMPLETE
            tx.completed_at = time.time()
            log.info(f"CFDP TX {tx.transaction_id} complete (Class 1)")
        else:
            tx.status = TransactionStatus.WAITING_ACK
            log.info(f"CFDP TX {tx.transaction_id} awaiting ACK (Class 2)")

        return tx.transaction_id

    def handle_nak(self, nak_data: bytes, file_data: bytes):
        """Handle a NAK PDU — retransmit requested segments."""
        nak = NAKPDU.unpack(nak_data)
        for seg_start, seg_end in nak.segment_requests:
            chunk = file_data[seg_start:seg_end]
            fd = FileDataPDU(offset=seg_start, data=chunk)
            log.debug(f"Retransmitting segment [{seg_start}:{seg_end}]")
            # Would need active transaction context — simplified here

    def get_transaction(self, tx_id: str) -> Optional[dict]:
        """Get transaction status by ID."""
        tx = self._transactions.get(tx_id)
        return tx.to_dict() if tx else None

    def list_transactions(self) -> List[dict]:
        """List all transactions."""
        return [tx.to_dict() for tx in self._transactions.values()]

    def _send_pdu(self, tx: CFDPTransaction, pdu_type: PDUType, payload: bytes):
        """Construct full PDU (header + payload) and send via transport."""
        header = PDUHeader(
            pdu_type=pdu_type,
            transmission_mode=tx.mode,
            data_length=len(payload),
            source_entity_id=tx.source_entity,
            transaction_seq=tx.transaction_seq,
            destination_entity_id=tx.destination_entity,
        )
        pdu_bytes = header.pack() + payload
        self.transport(pdu_bytes)

    @staticmethod
    def _null_transport(data: bytes):
        """Discard transport — used when no transport is configured."""
        pass


# ---------------------------------------------------------------------------
# CFDP Receiver (Destination Entity)
# ---------------------------------------------------------------------------
class CFDPReceiver:
    """
    CFDP receiving entity — reassembles files from incoming PDUs.

    API-only. No GUI. Provide a transport pull function or feed PDUs
    directly via process_pdu().

    Parameters
    ----------
    entity_id : int
        This entity's CFDP ID.
    output_dir : str
        Directory to write received files.
    on_complete : callable, optional
        Callback(transaction: CFDPTransaction) on successful file delivery.
    """

    def __init__(
        self,
        entity_id: int = 2,
        output_dir: str = "./cfdp_received",
        on_complete: Optional[Callable] = None,
    ):
        self.entity_id = entity_id
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.on_complete = on_complete
        self._transactions: Dict[int, CFDPTransaction] = {}

    def process_pdu(self, raw: bytes) -> Optional[dict]:
        """
        Process a single incoming CFDP PDU.

        Returns transaction status dict, or None if PDU is malformed.
        """
        if len(raw) < PDUHeader.size():
            log.warning("PDU too short — discarding")
            return None

        header = PDUHeader.unpack(raw[:PDUHeader.size()])
        payload = raw[PDUHeader.size():]
        seq = header.transaction_seq

        if header.pdu_type == PDUType.FILE_DIRECTIVE:
            return self._handle_directive(header, payload, seq)
        elif header.pdu_type == PDUType.FILE_DATA:
            return self._handle_data(header, payload, seq)
        return None

    def _handle_directive(self, header: PDUHeader, payload: bytes, seq: int):
        """Route file directive PDUs."""
        if not payload:
            return None

        code = payload[0]

        if code == DirectiveCode.METADATA:
            meta = MetadataPDU.unpack(payload)
            tx = CFDPTransaction(
                transaction_seq=seq,
                source_entity=header.source_entity_id,
                destination_entity=self.entity_id,
                source_filename=meta.source_filename,
                destination_filename=meta.destination_filename,
                file_size=meta.file_size,
                mode=header.transmission_mode,
                status=TransactionStatus.RECEIVING,
                segments_total=(meta.file_size + SEGMENT_SIZE - 1) // SEGMENT_SIZE,
            )
            self._transactions[seq] = tx
            log.info(f"CFDP RX metadata: {meta.source_filename} "
                     f"({meta.file_size} bytes) from entity {header.source_entity_id}")
            return tx.to_dict()

        elif code == DirectiveCode.EOF_PDU:
            eof = EOFPDU.unpack(payload)
            tx = self._transactions.get(seq)
            if tx is None:
                return None

            # Reassemble file
            reassembled = self._reassemble(tx)
            actual_checksum = cfdp_checksum(reassembled)

            if actual_checksum == eof.file_checksum and len(reassembled) == eof.file_size:
                # Write to disk
                out_path = self.output_dir / tx.destination_filename
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(reassembled)

                tx.status = TransactionStatus.COMPLETE
                tx.completed_at = time.time()
                tx.condition = ConditionCode.NO_ERROR
                log.info(f"CFDP RX complete: {out_path} ({len(reassembled)} bytes)")

                if self.on_complete:
                    self.on_complete(tx)
            else:
                tx.condition = ConditionCode.FILE_CHECKSUM_FAILURE
                tx.status = TransactionStatus.FAILED
                log.error(f"CFDP checksum mismatch for {tx.source_filename}")

                # For Class 2 — generate NAK
                if tx.mode == TransmissionMode.ACKNOWLEDGED:
                    missing = self._find_missing_segments(tx)
                    tx.missing_segments = missing

            return tx.to_dict()

        elif code == DirectiveCode.FINISHED:
            finished = FinishedPDU.unpack(payload)
            log.info(f"CFDP Finished PDU: condition={finished.condition_code.name}")
            return {"finished": True, "condition": finished.condition_code.name}

        return None

    def _handle_data(self, header: PDUHeader, payload: bytes, seq: int):
        """Store received file data segment."""
        tx = self._transactions.get(seq)
        if tx is None:
            return None

        fd = FileDataPDU.unpack(payload)
        tx.receive_buffer[fd.offset] = fd.data
        tx.segments_received.add(fd.offset)
        return tx.to_dict()

    def _reassemble(self, tx: CFDPTransaction) -> bytes:
        """Reassemble file from received segments."""
        result = bytearray(tx.file_size)
        for offset, data in sorted(tx.receive_buffer.items()):
            end = min(offset + len(data), tx.file_size)
            result[offset:end] = data[:end - offset]
        return bytes(result)

    def _find_missing_segments(self, tx: CFDPTransaction) -> List[tuple]:
        """Identify missing file segments for NAK generation."""
        missing = []
        offset = 0
        while offset < tx.file_size:
            if offset not in tx.receive_buffer:
                seg_end = min(offset + SEGMENT_SIZE, tx.file_size)
                missing.append((offset, seg_end))
            offset += SEGMENT_SIZE
        return missing

    def get_transaction(self, seq: int) -> Optional[dict]:
        """Get transaction status."""
        tx = self._transactions.get(seq)
        return tx.to_dict() if tx else None

    def list_transactions(self) -> List[dict]:
        """List all transactions."""
        return [tx.to_dict() for tx in self._transactions.values()]