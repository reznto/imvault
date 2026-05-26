"""Tests for imvault.crypto — encryption and decryption."""

import pytest

from imvault.crypto import (
    decrypt_archive,
    decrypt_archive_to_file,
    encrypt_archive,
    encrypt_archive_from_file,
    HEADER_SIZE,
)
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


class TestStreamingDecrypt:
    """`decrypt_archive_to_file` should produce identical output to the
    in-memory `decrypt_archive`, and should call the progress callback
    once per decrypted chunk."""

    def test_matches_in_memory_decrypt(self, tmp_path):
        # Multi-chunk payload so v2 chunked path is exercised.
        from imvault.constants import CHUNK_SIZE

        plaintext = b"A" * (CHUNK_SIZE * 3 + 17)
        password = "stream-test"
        encrypted = encrypt_archive(plaintext, password)

        encrypted_path = tmp_path / "archive.imv"
        encrypted_path.write_bytes(encrypted)
        output_path = tmp_path / "out.tar.gz"

        decrypt_archive_to_file(str(encrypted_path), str(output_path), password)

        assert output_path.read_bytes() == plaintext
        assert output_path.read_bytes() == decrypt_archive(encrypted, password)

    def test_progress_callback_called_per_chunk(self, tmp_path):
        from imvault.constants import CHUNK_SIZE

        # Three full chunks + a trailing partial.
        plaintext = b"B" * (CHUNK_SIZE * 3 + 100)
        password = "progress-test"
        encrypted = encrypt_archive(plaintext, password)

        encrypted_path = tmp_path / "archive.imv"
        encrypted_path.write_bytes(encrypted)
        output_path = tmp_path / "out.tar.gz"

        events: list[tuple[int, int]] = []
        decrypt_archive_to_file(
            str(encrypted_path),
            str(output_path),
            password,
            progress=lambda done, total: events.append((done, total)),
        )

        # 4 chunks total → 4 progress callbacks; total stays constant; done climbs.
        assert len(events) == 4
        assert all(total == 4 for _, total in events)
        assert [done for done, _ in events] == [1, 2, 3, 4]

    def test_wrong_password_raises(self, tmp_path):
        plaintext = b"some payload"
        encrypted = encrypt_archive(plaintext, "correct-pw")

        encrypted_path = tmp_path / "archive.imv"
        encrypted_path.write_bytes(encrypted)
        output_path = tmp_path / "out.tar.gz"

        with pytest.raises(ValueError, match="wrong password"):
            decrypt_archive_to_file(
                str(encrypted_path), str(output_path), "wrong-pw"
            )

    def test_truncated_archive_raises(self, tmp_path):
        plaintext = b"C" * 1000
        encrypted = encrypt_archive(plaintext, "pw")

        encrypted_path = tmp_path / "archive.imv"
        encrypted_path.write_bytes(encrypted[:-20])  # chop the tail
        output_path = tmp_path / "out.tar.gz"

        with pytest.raises(ValueError):
            decrypt_archive_to_file(
                str(encrypted_path), str(output_path), "pw"
            )


class TestStreamingEncrypt:
    """`encrypt_archive_from_file` should produce output that the regular
    `decrypt_archive` can read identically — the streaming write side is a
    drop-in for the in-memory `encrypt_archive`."""

    def test_round_trip_via_streaming_encrypt(self, tmp_path):
        from imvault.constants import CHUNK_SIZE

        plaintext = b"streaming-encrypt round trip " * 1000  # ~30 KB
        password = "encrypt-stream-test"

        input_path = tmp_path / "plain.tar.gz"
        input_path.write_bytes(plaintext)
        output_path = tmp_path / "encrypted.imv"

        encrypt_archive_from_file(str(input_path), str(output_path), password)

        # The in-memory decrypt should recover the original plaintext exactly.
        decrypted = decrypt_archive(output_path.read_bytes(), password)
        assert decrypted == plaintext

    def test_multi_chunk_round_trip(self, tmp_path):
        from imvault.constants import CHUNK_SIZE

        plaintext = b"X" * (CHUNK_SIZE * 3 + 4242)  # 3 full chunks + partial
        password = "multi-chunk-stream"

        input_path = tmp_path / "plain.tar.gz"
        input_path.write_bytes(plaintext)
        output_path = tmp_path / "encrypted.imv"

        encrypt_archive_from_file(str(input_path), str(output_path), password)

        decrypted = decrypt_archive(output_path.read_bytes(), password)
        assert decrypted == plaintext

    def test_empty_input(self, tmp_path):
        input_path = tmp_path / "empty.tar.gz"
        input_path.write_bytes(b"")
        output_path = tmp_path / "encrypted.imv"

        events: list[tuple[int, int]] = []
        encrypt_archive_from_file(
            str(input_path),
            str(output_path),
            "pw",
            progress=lambda done, total: events.append((done, total)),
        )

        decrypted = decrypt_archive(output_path.read_bytes(), "pw")
        assert decrypted == b""
        assert events == [(0, 0)]

    def test_progress_callback_per_chunk(self, tmp_path):
        from imvault.constants import CHUNK_SIZE

        plaintext = b"Y" * (CHUNK_SIZE * 4)  # exactly 4 chunks
        password = "encrypt-progress"

        input_path = tmp_path / "plain.tar.gz"
        input_path.write_bytes(plaintext)
        output_path = tmp_path / "encrypted.imv"

        events: list[tuple[int, int]] = []
        encrypt_archive_from_file(
            str(input_path),
            str(output_path),
            password,
            progress=lambda done, total: events.append((done, total)),
        )

        assert len(events) == 4
        assert all(total == 4 for _, total in events)
        assert [done for done, _ in events] == [1, 2, 3, 4]

    def test_output_decodable_by_streaming_decrypt(self, tmp_path):
        """The two streaming halves should be fully symmetric — encrypt via
        streaming, then decrypt via streaming."""
        from imvault.constants import CHUNK_SIZE

        plaintext = b"symmetric stream test " * 5000
        password = "symmetric"

        plain_path = tmp_path / "plain.tar.gz"
        plain_path.write_bytes(plaintext)
        encrypted_path = tmp_path / "out.imv"
        recovered_path = tmp_path / "recovered.tar.gz"

        encrypt_archive_from_file(str(plain_path), str(encrypted_path), password)
        decrypt_archive_to_file(str(encrypted_path), str(recovered_path), password)

        assert recovered_path.read_bytes() == plaintext
