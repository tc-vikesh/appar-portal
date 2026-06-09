import hashlib
import hmac
import time
from django.conf import settings
from rest_framework import authentication, exceptions

class HMACAuthentication(authentication.BaseAuthentication):
    """
    Custom authentication for DRF views verifying HMAC signatures.
    Headers:
        X-Client-ID
        X-Client-Timestamp
        X-Client-HMAC
    """
    def authenticate(self, request):
        client_id = request.META.get('HTTP_X_CLIENT_ID')
        timestamp = request.META.get('HTTP_X_CLIENT_TIMESTAMP')
        received_hmac = request.META.get('HTTP_X_CLIENT_HMAC')

        # HMAC headers are strictly mandatory for these endpoints.
        # If any header is missing, fail authentication immediately.
        if not client_id or not timestamp or not received_hmac:
            raise exceptions.AuthenticationFailed('Missing required HMAC authentication headers.')

        # Retrieve settings
        expected_client_id = getattr(settings, 'ABC_CLIENT_ID', None)
        client_secret = getattr(settings, 'ABC_CLIENT_SECRET', None)

        if not expected_client_id or not client_secret:
            raise exceptions.AuthenticationFailed('HMAC authentication is not configured on the server.')

        if client_id != expected_client_id:
            raise exceptions.AuthenticationFailed('Invalid client identity.')

        # Validate timestamp window: -600s to +300s from server time
        try:
            ts = float(timestamp)
        except ValueError:
            raise exceptions.AuthenticationFailed('Invalid timestamp format.')

        current_time = time.time()
        if ts < (current_time - 600) or ts > (current_time + 300):
            raise exceptions.AuthenticationFailed('Timestamp is outside the valid window.')

        # Calculate signature: SHA256(secret + id + timestamp)
        message = f"{client_secret}{client_id}{timestamp}"
        calculated_signature = hashlib.sha256(message.encode('utf-8')).hexdigest()

        # Secure comparison using hmac.compare_digest
        if not hmac.compare_digest(calculated_signature, received_hmac):
            raise exceptions.AuthenticationFailed('Invalid HMAC signature.')

        # Return anonymous/system user for DRF request.user (M2M auth)
        return (None, None)
