"""imvault — Archive iMessage conversations into encrypted, portable .imv files."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("imvault")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
