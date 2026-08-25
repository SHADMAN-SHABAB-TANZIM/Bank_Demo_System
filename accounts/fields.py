import base64
import hashlib
import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


def _get_fernet():

    """
    Derives a Fernet key from FIELD_ENCRYPTION_KEY (or, if that
    isn't set, from SECRET_KEY) so the field works out of the
    box. For a real deployment, set FIELD_ENCRYPTION_KEY to its
    own independent secret in settings/environment - rotating
    SECRET_KEY would otherwise make existing encrypted values
    undecryptable.
    """

    key_material = getattr(settings, "FIELD_ENCRYPTION_KEY", None) or settings.SECRET_KEY
    digest = hashlib.sha256(key_material.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


class EncryptedCharField(models.CharField):

    """
    Transparently encrypts field values at rest using Fernet
    (AES128-CBC + HMAC). Inspired by the Oracle Transparent
    Data Encryption used on CUSTOMERS.NID and CUSTOMERS.PHONE
    in the original schema (`ENCRYPT USING 'AES256'`).

    Falls back to storing plaintext - with a one-time warning
    in the logs - if the `cryptography` package isn't
    installed, so the project keeps working without it; run
    `pip install cryptography` to turn encryption on.

    Note: because Fernet encryption is non-deterministic (each
    encrypted value differs even for the same input), this
    field cannot be used in exact-match filters/lookups. It is
    intended for values that are only ever displayed, never
    queried against - e.g. a customer's NID or phone number.
    """

    _warned = False

    def __init__(self, *args, **kwargs):

        # Encrypted values are longer than the plaintext
        # (base64 + Fernet overhead + timestamp/nonce), so the
        # underlying column needs generous headroom.
        kwargs.setdefault("max_length", 255)

        super().__init__(*args, **kwargs)

    def _warn_once(self):

        if not EncryptedCharField._warned:

            logger.warning(
                "cryptography package not installed - "
                "EncryptedCharField is storing values in "
                "PLAINTEXT. Run 'pip install cryptography' "
                "to enable encryption."
            )

            EncryptedCharField._warned = True

    def get_prep_value(self, value):

        value = super().get_prep_value(value)

        if value is None or value == "":
            return value

        if not CRYPTO_AVAILABLE:
            self._warn_once()
            return value

        return _get_fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):

        if value is None or value == "":
            return value

        if not CRYPTO_AVAILABLE:
            return value

        try:

            return _get_fernet().decrypt(value.encode()).decode()

        except InvalidToken:

            # Stored before encryption was enabled, or under a
            # different key. Return as-is rather than crashing.
            return value

        except Exception:

            return value
