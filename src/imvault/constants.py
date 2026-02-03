"""Constants used across imvault modules."""

from datetime import datetime, timezone

# .imv file format
MAGIC = b"IMVAULT1"
FORMAT_VERSION = 1

# Argon2id KDF parameters
ARGON2_MEMORY_COST = 65536  # 64 MB
ARGON2_TIME_COST = 3        # iterations
ARGON2_PARALLELISM = 4
SALT_LENGTH = 16
NONCE_LENGTH = 12
KEY_LENGTH = 32             # AES-256

# Apple Core Data epoch: 2001-01-01 00:00:00 UTC
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
# Nanosecond threshold — timestamps above this are nanoseconds, below are seconds.
# Roughly corresponds to the year 2030 in seconds.
NANOSECOND_THRESHOLD = 1_000_000_000_000

# Tapback (reaction) types: associated_message_type values
# 2000-2005 = add reaction, 3000-3005 = remove reaction
TAPBACK_MAP = {
    2000: "Loved",
    2001: "Liked",
    2002: "Disliked",
    2003: "Laughed",
    2004: "Emphasized",
    2005: "Questioned",
    3000: "-Loved",
    3001: "-Liked",
    3002: "-Disliked",
    3003: "-Laughed",
    3004: "-Emphasized",
    3005: "-Questioned",
}

# Chunk size for the HTML reader (messages loaded per page)
READER_CHUNK_SIZE = 200
