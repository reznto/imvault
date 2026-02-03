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
    FORMAT_VERSION,
    KEY_LENGTH,
    MAGIC,
    NONCE_LENGTH,
    SALT_LENGTH,
)

# Header layout:
#   8 bytes  — MAGIC ("IMVAULT1")
#   2 bytes  — version (uint16 LE)
#  16 bytes  — salt
#  12 bytes  — nonce
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


def encrypt_archive(plaintext: bytes, password: str) -> bytes:
    """Encrypt a tar.gz payload into .imv format.

    Returns bytes: header || AES-256-GCM(plaintext, aad=header).
    """
    salt = os.urandom(SALT_LENGTH)
    nonce = os.urandom(NONCE_LENGTH)
    key = derive_key(password, salt)

    # Build header
    header = bytearray()
    header.extend(MAGIC)
    header.extend(struct.pack("<H", FORMAT_VERSION))
    header.extend(salt)
    header.extend(nonce)
    header = bytes(header)

    assert len(header) == HEADER_SIZE

    # Encrypt with header as AAD (authenticated associated data)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, header)

    return header + ciphertext


def decrypt_archive(data: bytes, password: str) -> bytes:
    """Decrypt an .imv file back to the tar.gz payload.

    Raises:
        ValueError: If the file is not a valid .imv archive.
        cryptography.exceptions.InvalidTag: If the password is wrong or data is tampered.
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
    (version,) = struct.unpack("<H", data[offset: offset + 2])
    offset += 2

    if version != FORMAT_VERSION:
        raise ValueError(
            f"Unsupported archive version {version} (expected {FORMAT_VERSION})."
        )

    salt = data[offset: offset + SALT_LENGTH]
    offset += SALT_LENGTH

    nonce = data[offset: offset + NONCE_LENGTH]
    offset += NONCE_LENGTH

    assert offset == HEADER_SIZE

    header = data[:HEADER_SIZE]
    ciphertext = data[HEADER_SIZE:]

    key = derive_key(password, salt)
    aesgcm = AESGCM(key)

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, header)
    except InvalidTag as e:
        raise ValueError(
            "Decryption failed — wrong password or corrupted archive."
        ) from e

    return plaintext
