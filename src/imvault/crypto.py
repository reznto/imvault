"""AES-256-GCM encryption with Argon2id key derivation for .imv archives."""

import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .constants import (
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    CHUNK_SIZE,
    FORMAT_VERSION,
    KEY_LENGTH,
    MAGIC,
    NONCE_LENGTH,
    SALT_LENGTH,
)

# Header layout (v1 and v2):
#   8 bytes  — MAGIC ("IMVAULT1")
#   2 bytes  — version (uint16 LE)
#  16 bytes  — salt
#  12 bytes  — nonce (base nonce for v2 chunked encryption)
# ─────────── total: 38 bytes
HEADER_SIZE = len(MAGIC) + 2 + SALT_LENGTH + NONCE_LENGTH


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 256-bit key from a password using Argon2id.

    Falls back to scrypt if argon2-cffi is not installed (cryptography doesn't
    bundle Argon2 on all platforms).
    """
    password_bytes = password.encode("utf-8")

    # Try Argon2id first
    try:
        from argon2.low_level import Type, hash_secret_raw

        return hash_secret_raw(
            secret=password_bytes,
            salt=salt,
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            hash_len=KEY_LENGTH,
            type=Type.ID,
        )
    except ImportError:
        pass

    # Fallback: scrypt with comparable security parameters
    kdf = Scrypt(salt=salt, length=KEY_LENGTH, n=2**17, r=8, p=1)
    return kdf.derive(password_bytes)


def _increment_nonce(nonce: bytes, increment: int) -> bytes:
    """Increment a 12-byte nonce by a counter value (little-endian)."""
    nonce_int = int.from_bytes(nonce, "little")
    nonce_int += increment
    return (nonce_int & ((1 << 96) - 1)).to_bytes(12, "little")


def encrypt_archive(plaintext: bytes, password: str) -> bytes:
    """Encrypt a tar.gz payload into .imv format (v2 chunked encryption).

    Format v2:
        header (38 bytes) || chunk_count (4 bytes, uint32 LE)
        || for each chunk: chunk_len (4 bytes) || ciphertext

    Each chunk uses nonce + chunk_index to ensure unique nonces.
    """
    salt = os.urandom(SALT_LENGTH)
    base_nonce = os.urandom(NONCE_LENGTH)
    key = derive_key(password, salt)

    # Build header
    header = bytearray()
    header.extend(MAGIC)
    header.extend(struct.pack("<H", FORMAT_VERSION))
    header.extend(salt)
    header.extend(base_nonce)
    header = bytes(header)

    assert len(header) == HEADER_SIZE

    aesgcm = AESGCM(key)

    # Split plaintext into chunks and encrypt each
    chunks = []
    offset = 0
    chunk_index = 0
    while offset < len(plaintext):
        chunk_data = plaintext[offset : offset + CHUNK_SIZE]
        chunk_nonce = _increment_nonce(base_nonce, chunk_index)
        # AAD includes header + chunk index for binding
        aad = header + struct.pack("<I", chunk_index)
        ciphertext = aesgcm.encrypt(chunk_nonce, chunk_data, aad)
        chunks.append(ciphertext)
        offset += CHUNK_SIZE
        chunk_index += 1

    # Build output: header || chunk_count || (chunk_len || chunk_data)*
    output = bytearray(header)
    output.extend(struct.pack("<I", len(chunks)))
    for chunk in chunks:
        output.extend(struct.pack("<I", len(chunk)))
        output.extend(chunk)

    return bytes(output)


def decrypt_archive(data: bytes, password: str) -> bytes:
    """Decrypt an .imv file back to the tar.gz payload.

    Supports both v1 (single-block) and v2 (chunked) formats.

    Raises:
        ValueError: If the file is not a valid .imv archive.
        ValueError: If the password is wrong or data is tampered.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError("File is too small to be a valid .imv archive.")

    # Parse header
    magic = data[: len(MAGIC)]
    if magic != MAGIC:
        raise ValueError(
            f"Not an imvault archive (expected magic {MAGIC!r}, got {magic!r})."
        )

    offset = len(MAGIC)
    (version,) = struct.unpack("<H", data[offset : offset + 2])
    offset += 2

    if version not in (1, 2):
        raise ValueError(
            f"Unsupported archive version {version} (expected 1 or 2)."
        )

    salt = data[offset : offset + SALT_LENGTH]
    offset += SALT_LENGTH

    base_nonce = data[offset : offset + NONCE_LENGTH]
    offset += NONCE_LENGTH

    assert offset == HEADER_SIZE

    header = data[:HEADER_SIZE]
    key = derive_key(password, salt)
    aesgcm = AESGCM(key)

    try:
        if version == 1:
            # v1: single ciphertext block after header
            ciphertext = data[HEADER_SIZE:]
            plaintext = aesgcm.decrypt(base_nonce, ciphertext, header)
        else:
            # v2: chunked encryption
            pos = HEADER_SIZE
            if len(data) < pos + 4:
                raise ValueError("Truncated archive (missing chunk count).")
            (chunk_count,) = struct.unpack("<I", data[pos : pos + 4])
            pos += 4

            plaintext_chunks = []
            for chunk_index in range(chunk_count):
                if len(data) < pos + 4:
                    raise ValueError(f"Truncated archive at chunk {chunk_index}.")
                (chunk_len,) = struct.unpack("<I", data[pos : pos + 4])
                pos += 4
                if len(data) < pos + chunk_len:
                    raise ValueError(f"Truncated archive at chunk {chunk_index}.")
                chunk_ciphertext = data[pos : pos + chunk_len]
                pos += chunk_len

                chunk_nonce = _increment_nonce(base_nonce, chunk_index)
                aad = header + struct.pack("<I", chunk_index)
                chunk_plaintext = aesgcm.decrypt(chunk_nonce, chunk_ciphertext, aad)
                plaintext_chunks.append(chunk_plaintext)

            plaintext = b"".join(plaintext_chunks)

    except InvalidTag as e:
        raise ValueError(
            "Decryption failed — wrong password or corrupted archive."
        ) from e

    return plaintext
