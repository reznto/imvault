"""Resolve phone numbers and emails to contact names via macOS Contacts."""

import logging
import re

logger = logging.getLogger(__name__)

_DIGITS_RE = re.compile(r"\D")


def _normalize_phone(phone: str) -> list[str]:
    """Return normalized variants of a phone number for lookup.

    Strips non-digit characters and produces variants with/without leading
    country code so that +15551234567 matches 5551234567 and vice-versa.
    """
    digits = _DIGITS_RE.sub("", phone)
    if not digits:
        return []
    variants = [digits]
    # 11-digit US numbers starting with 1: also index without the leading 1
    if len(digits) == 11 and digits.startswith("1"):
        variants.append(digits[1:])
    # 10-digit numbers: also index with leading 1
    if len(digits) == 10:
        variants.append("1" + digits)
    return variants


class ContactResolver:
    """Map phone numbers and email addresses to human-readable contact names.

    Uses the macOS Contacts framework (CNContactStore) via pyobjc.  If the
    framework is unavailable or permission is denied the resolver silently
    degrades — ``resolve()`` returns ``None`` for every query.
    """

    def __init__(self) -> None:
        self._lookup: dict[str, str] = {}
        self._loaded = False
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, identifier: str) -> str | None:
        """Return a display name for *identifier*, or ``None``."""
        if not self._lookup:
            return None

        key = identifier.strip().lower()

        # Direct email hit
        if key in self._lookup:
            return self._lookup[key]

        # Phone: normalise and try all variants
        for variant in _normalize_phone(identifier):
            if variant in self._lookup:
                return self._lookup[variant]

        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            import Contacts  # pyobjc-framework-Contacts
        except ImportError:
            logger.warning(
                "pyobjc-framework-Contacts not installed — "
                "contact name resolution disabled"
            )
            return

        store = Contacts.CNContactStore.alloc().init()

        keys_to_fetch = [
            Contacts.CNContactGivenNameKey,
            Contacts.CNContactFamilyNameKey,
            Contacts.CNContactPhoneNumbersKey,
            Contacts.CNContactEmailAddressesKey,
        ]

        # Fetch contacts from every container (iCloud, Google, Exchange, …).
        # macOS automatically shows the permission dialog on first access.
        try:
            containers, error = store.containersMatchingPredicate_error_(
                None, None
            )
        except Exception:
            containers = None
            error = True

        if error or not containers:
            logger.warning(
                "Contacts access denied — contact name resolution disabled"
            )
            return

        for container in containers:
            predicate = (
                Contacts.CNContact
                .predicateForContactsInContainerWithIdentifier_(
                    container.identifier()
                )
            )
            try:
                contacts, error = (
                    store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                        predicate, keys_to_fetch, None
                    )
                )
            except Exception:
                continue
            if contacts:
                for contact in contacts:
                    self._index_contact(contact)

        self._loaded = True
        logger.debug("Indexed %d contact identifiers", len(self._lookup))

    def _index_contact(self, contact) -> None:
        """Add all phone/email variants for *contact* to the lookup dict."""
        given = contact.givenName() or ""
        family = contact.familyName() or ""
        name = f"{given} {family}".strip()
        if not name:
            return

        # Index phone numbers
        for phone_value in contact.phoneNumbers():
            raw = phone_value.value().stringValue()
            for variant in _normalize_phone(raw):
                self._lookup[variant] = name

        # Index email addresses
        for email_value in contact.emailAddresses():
            email = email_value.value()
            if email:
                self._lookup[email.strip().lower()] = name
