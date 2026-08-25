from django.core.management.base import BaseCommand

from accounts.models import Customer


class Command(BaseCommand):

    """
    Re-saves every customer's phone/nid so any pre-existing
    plaintext values (from before EncryptedCharField was
    added, or from before `cryptography` was installed) get
    encrypted at rest. Safe to run repeatedly - already
    encrypted values are simply re-encrypted with a fresh
    nonce, which is harmless.
    """

    help = (
        "Encrypt (or re-encrypt) phone/nid for all existing "
        "customers. Run once after installing 'cryptography', "
        "and again any time FIELD_ENCRYPTION_KEY changes."
    )

    def handle(self, *args, **options):

        from accounts.fields import CRYPTO_AVAILABLE

        if not CRYPTO_AVAILABLE:

            self.stdout.write(
                self.style.WARNING(
                    "The 'cryptography' package isn't installed. "
                    "Run 'pip install cryptography' first, then "
                    "re-run this command."
                )
            )

            return

        count = 0

        for customer in Customer.objects.all():

            customer.save(update_fields=["phone", "nid"])
            count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Encrypted phone/nid for {count} customer(s)."
            )
        )
