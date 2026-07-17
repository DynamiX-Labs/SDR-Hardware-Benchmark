"""
Tests for CCSDS CFDP and Federation modules.
DynamiX Labs | Phase 4
"""
import pytest
import sys, os, time, tempfile, struct
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ──────────────────────────────────────────────────────────────────────
# CFDP PDU Tests
# ──────────────────────────────────────────────────────────────────────
class TestCFDPHeader:
    def test_header_pack_unpack(self):
        from protocols.cfdp import PDUHeader, PDUType, TransmissionMode
        h = PDUHeader(pdu_type=PDUType.FILE_DATA, source_entity_id=1,
                      transaction_seq=42, destination_entity_id=2, data_length=1024)
        packed = h.pack()
        restored = PDUHeader.unpack(packed)
        assert restored.source_entity_id == 1
        assert restored.transaction_seq == 42
        assert restored.data_length == 1024
        assert restored.pdu_type == PDUType.FILE_DATA

    def test_header_size(self):
        from protocols.cfdp import PDUHeader
        assert PDUHeader.size() == 7


class TestCFDPMetadata:
    def test_metadata_roundtrip(self):
        from protocols.cfdp import MetadataPDU
        m = MetadataPDU(file_size=4096, source_filename="test.bin",
                        destination_filename="/data/test.bin")
        packed = m.pack()
        restored = MetadataPDU.unpack(packed)
        assert restored.file_size == 4096
        assert restored.source_filename == "test.bin"
        assert restored.destination_filename == "/data/test.bin"


class TestCFDPEOF:
    def test_eof_roundtrip(self):
        from protocols.cfdp import EOFPDU, ConditionCode
        e = EOFPDU(condition_code=ConditionCode.NO_ERROR,
                   file_checksum=0xDEADBEEF, file_size=2048)
        packed = e.pack()
        restored = EOFPDU.unpack(packed)
        assert restored.file_checksum == 0xDEADBEEF
        assert restored.file_size == 2048
        assert restored.condition_code == ConditionCode.NO_ERROR


class TestCFDPNAK:
    def test_nak_roundtrip(self):
        from protocols.cfdp import NAKPDU
        n = NAKPDU(scope_start=0, scope_end=4096,
                   segment_requests=[(0, 1024), (2048, 3072)])
        packed = n.pack()
        restored = NAKPDU.unpack(packed)
        assert restored.scope_start == 0
        assert restored.scope_end == 4096
        assert len(restored.segment_requests) == 2
        assert restored.segment_requests[0] == (0, 1024)


class TestCFDPChecksum:
    def test_checksum_deterministic(self):
        from protocols.cfdp import cfdp_checksum
        data = b"Hello CCSDS CFDP!"
        c1 = cfdp_checksum(data)
        c2 = cfdp_checksum(data)
        assert c1 == c2

    def test_checksum_different_data(self):
        from protocols.cfdp import cfdp_checksum
        assert cfdp_checksum(b"aaa") != cfdp_checksum(b"bbb")


# ──────────────────────────────────────────────────────────────────────
# CFDP Sender/Receiver Integration
# ──────────────────────────────────────────────────────────────────────
class TestCFDPSenderReceiver:
    def test_class1_file_transfer(self, tmp_path):
        """Class 1 (unreliable) file transfer through sender→receiver."""
        from protocols.cfdp import CFDPSender, CFDPReceiver, TransmissionMode

        # Create test file
        test_file = tmp_path / "payload.bin"
        test_data = os.urandom(3000)
        test_file.write_bytes(test_data)

        rx_dir = tmp_path / "received"
        pdu_buffer = []

        # Wire sender to buffer
        sender = CFDPSender(
            entity_id=1, peer_id=2,
            transport=lambda pdu: pdu_buffer.append(pdu),
            segment_size=512,
            transmission_mode=TransmissionMode.UNACKNOWLEDGED,
        )

        # Send file
        tx_id = sender.put(str(test_file), destination_path="payload.bin")
        assert tx_id is not None
        assert len(pdu_buffer) > 0

        # Feed all PDUs to receiver
        receiver = CFDPReceiver(entity_id=2, output_dir=str(rx_dir))
        for pdu in pdu_buffer:
            receiver.process_pdu(pdu)

        # Verify received file
        received_file = rx_dir / "payload.bin"
        assert received_file.exists()
        assert received_file.read_bytes() == test_data

    def test_sender_transaction_tracking(self, tmp_path):
        from protocols.cfdp import CFDPSender, TransmissionMode
        test_file = tmp_path / "small.bin"
        test_file.write_bytes(b"test data 12345")

        sender = CFDPSender(entity_id=1, peer_id=2, segment_size=1024)
        tx_id = sender.put(str(test_file))

        tx_info = sender.get_transaction(tx_id)
        assert tx_info is not None
        assert tx_info["status"] == "complete"
        assert tx_info["file_size"] == 15

    def test_sender_file_not_found(self):
        from protocols.cfdp import CFDPSender
        sender = CFDPSender()
        with pytest.raises(FileNotFoundError):
            sender.put("/nonexistent/file.bin")

    def test_list_transactions(self, tmp_path):
        from protocols.cfdp import CFDPSender
        test_file = tmp_path / "data.bin"
        test_file.write_bytes(b"x" * 100)
        sender = CFDPSender()
        sender.put(str(test_file))
        txs = sender.list_transactions()
        assert len(txs) >= 1


class TestCFDPTransaction:
    def test_transaction_to_dict(self):
        from protocols.cfdp import CFDPTransaction, TransactionStatus
        tx = CFDPTransaction(
            source_entity=1, destination_entity=2,
            file_size=4096, segments_total=4,
            status=TransactionStatus.ACTIVE,
        )
        d = tx.to_dict()
        assert d["file_size"] == 4096
        assert d["status"] == "active"
        assert d["progress_pct"] == 0.0


# ──────────────────────────────────────────────────────────────────────
# Federation Model Tests
# ──────────────────────────────────────────────────────────────────────
class TestFederationModels:
    def test_station_info(self):
        from federation import StationInfo
        s = StationInfo(station_id="GS-TEST", latitude=13.08, longitude=80.27)
        d = s.to_dict()
        assert d["station_id"] == "GS-TEST"
        restored = StationInfo.from_dict(d)
        assert restored.latitude == 13.08

    def test_telemetry_share(self):
        from federation import TelemetryShare
        t = TelemetryShare(
            station_id="GS-1", satellite="NOAA-15",
            decoder="apt", payload={"temp": 22.5},
        )
        d = t.to_dict()
        assert d["satellite"] == "NOAA-15"
        restored = TelemetryShare.from_dict(d)
        assert restored.payload["temp"] == 22.5

    def test_pass_coordination(self):
        from federation import PassCoordination
        p = PassCoordination(
            satellite="ISS", requesting_station="GS-1",
            aos_utc="2026-05-07T12:00:00Z", max_elevation_deg=45.0,
        )
        assert p.status == "available"
        d = p.to_dict()
        assert d["max_elevation_deg"] == 45.0


class TestFederationHub:
    def test_hub_creation(self):
        try:
            from federation import FederationHub
            hub = FederationHub()
            assert hub.bind_addr == "tcp://*:6000"
            assert len(hub.get_stations()) == 0
        except ImportError:
            pytest.skip("pyzmq not installed")


class TestFederationNode:
    def test_node_creation(self):
        try:
            from federation import FederationNode
            node = FederationNode(
                station_id="GS-TEST", lat=13.08, lon=80.27,
                capabilities=["apt", "adsb"],
            )
            assert node.station_id == "GS-TEST"
            assert node.capabilities == ["apt", "adsb"]
        except ImportError:
            pytest.skip("pyzmq not installed")

    def test_event_callback_registration(self):
        try:
            from federation import FederationNode
            node = FederationNode(station_id="GS-TEST")
            calls = []
            node.on_event("telemetry.new", lambda d: calls.append(d))
            assert len(node._event_callbacks["telemetry.new"]) == 1
        except ImportError:
            pytest.skip("pyzmq not installed")
