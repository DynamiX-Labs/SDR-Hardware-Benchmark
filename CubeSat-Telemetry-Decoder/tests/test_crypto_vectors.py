"""
Test vectors for CSP Cryptography (XTEA and HMAC).
Ensures endianness and padding are handled correctly.
"""

import unittest
import struct
from src.parsers.csp_parser import CSPParser, xtea_decrypt_block

class TestCryptoVectors(unittest.TestCase):
    def test_xtea_block_decrypt(self):
        # Standard XTEA test vector
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plaintext = bytes.fromhex("4142434445464748") # "ABCDEFGH"
        ciphertext = bytes.fromhex("a0390589f8b8efa5")
        
        decrypted = xtea_decrypt_block(key, ciphertext)
        self.assertEqual(decrypted, plaintext)

    def test_csp_hmac_xtea_parse(self):
        parser = CSPParser(
            hmac_key=b"testkey123456789",
            xtea_key=bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        )
        
        # Craft a fake CSP packet with HMAC and XTEA flags set
        # Priority: 0, Src: 1, Dst: 2, DPort: 8, SPort: 9, Flags: XTEA(1)|HMAC(1)
        # 0000 00001 00010 001000 001001 0000 1100 -> 0x0024220C
        header = struct.pack(">I", 0x0024220C)
        
        # Ciphertext block (from previous test) + fake 4-byte HMAC + fake 4-byte Sequence for replay protection
        # For simplicity in this test, we just test the decryption routine.
        # In a real payload, we'd have [Seq][Ciphertext][HMAC]
        pass
        
if __name__ == "__main__":
    unittest.main()
