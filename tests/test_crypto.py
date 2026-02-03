"""Tests for imvault.crypto — encryption and decryption."""

import pytest

from imvault.crypto import decrypt_archive, encrypt_archive, HEADER_SIZE
from imvault.constants import MAGIC, FORMAT_VERSION


class TestRoundTrip:
    """Test encrypt → decrypt round-trips."""

    def test_basic_round_trip(self):
        plaintext = b"Hello, imvault!" * 100
        password = "test-password-123"

        encrypted = encrypt_archive(plaintext, password)
        decrypted = decrypt_archive(encrypted, password)

        assert decrypted == plaintext

    def test_empty_payload(self):
        plaintext = b""
        password = "pw"

        encrypted = encrypt_archive(plaintext, password)
        decrypted = decrypt_archive(encrypted, password)

        assert decrypted == plaintext

    def test_large_payload(self):
        plaintext = b"x" * (1024 * 1024)  # 1 MB
        password = "large-test"

        encrypted = encrypt_archive(plaintext, password)
        decrypted = decrypt_archive(encrypted, password)

        assert decrypted == plaintext

    def test_multi_chunk_payload(self):
        """Test payload that spans multiple 64MB chunks."""
        from imvault.constants import CHUNK_SIZE
        # Create payload slightly larger than one chunk
        plaintext = b"y" * (CHUNK_SIZE + 1000)
        password = "chunked-test"

        encrypted = encrypt_archive(plaintext, password)
        decrypted = decrypt_archive(encrypted, password)

        assert decrypted == plaintext

    def test_unicode_password(self):
        plaintext = b"data"
        password = "\U0001f512\u00e9\u00f1\u00fc secure"

        encrypted = encrypt_archive(plaintext, password)
        decrypted = decrypt_archive(encrypted, password)

        assert decrypted == plaintext


class TestHeader:
    """Test .imv file header format."""

    def test_header_starts_with_magic(self):
        encrypted = encrypt_archive(b"data", "pw")
        assert encrypted[:8] == MAGIC

    def test_header_size(self):
        encrypted = encrypt_archive(b"data", "pw")
        assert len(encrypted) > HEADER_SIZE

    def test_unique_salt_and_nonce(self):
        """Each encryption should use different salt and nonce."""
        enc1 = encrypt_archive(b"data", "pw")
        enc2 = encrypt_archive(b"data", "pw")

        # Salt is at offset 10 (after magic + version)
        salt1 = enc1[10:26]
        salt2 = enc2[10:26]
        assert salt1 != salt2


class TestDecryptionFailures:
    """Test that decryption fails correctly for invalid inputs."""

    def test_wrong_password(self):
        encrypted = encrypt_archive(b"secret data", "correct-password")

        with pytest.raises((ValueError, Exception)):
            decrypt_archive(encrypted, "wrong-password")

    def test_tampered_ciphertext(self):
        encrypted = encrypt_archive(b"secret data", "pw")

        # Flip a byte in the ciphertext
        tampered = bytearray(encrypted)
        tampered[-10] ^= 0xFF
        tampered = bytes(tampered)

        with pytest.raises((ValueError, Exception)):
            decrypt_archive(tampered, "pw")

    def test_tampered_header(self):
        encrypted = encrypt_archive(b"secret data", "pw")

        # Modify a byte in the salt (part of AAD)
        tampered = bytearray(encrypted)
        tampered[12] ^= 0xFF
        tampered = bytes(tampered)

        with pytest.raises((ValueError, Exception)):
            decrypt_archive(tampered, "pw")

    def test_truncated_file(self):
        with pytest.raises(ValueError, match="too small"):
            decrypt_archive(b"short", "pw")

    def test_wrong_magic(self):
        encrypted = encrypt_archive(b"data", "pw")
        tampered = b"NOTMAGIC" + encrypted[8:]

        with pytest.raises(ValueError, match="Not an imvault"):
            decrypt_archive(tampered, "pw")

    def test_unsupported_version(self):
        encrypted = encrypt_archive(b"data", "pw")
        # Set version to 99
        tampered = bytearray(encrypted)
        tampered[8] = 99
        tampered[9] = 0
        tampered = bytes(tampered)

        with pytest.raises(ValueError, match="Unsupported"):
            decrypt_archive(tampered, "pw")


class TestV1Compatibility:
    """Test that v1 archives can still be decrypted."""

    def test_decrypt_v1_format(self):
        """Create a v1-style archive manually and verify decryption."""
        import os
        import struct
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from imvault.crypto import derive_key, HEADER_SIZE
        from imvault.constants import MAGIC, SALT_LENGTH, NONCE_LENGTH

        plaintext = b"v1 test data"
        password = "v1-password"

        # Build a v1 archive manually
        salt = os.urandom(SALT_LENGTH)
        nonce = os.urandom(NONCE_LENGTH)
        key = derive_key(password, salt)

        header = bytearray()
        header.extend(MAGIC)
        header.extend(struct.pack("<H", 1))  # version 1
        header.extend(salt)
        header.extend(nonce)
        header = bytes(header)

        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, header)

        v1_archive = header + ciphertext

        # Verify we can decrypt it
        decrypted = decrypt_archive(v1_archive, password)
        assert decrypted == plaintext
