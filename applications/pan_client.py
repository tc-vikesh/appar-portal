import time
import requests
from django.conf import settings
from applications.models import PANApiLog

class PANClient:
    """
    Client for interacting with the Cashfree PAN Verification Sandbox API.
    Per Constitution P1, all outbound calls are logged in PANApiLog inside a finally block.
    Per Constitution P6, all sensitive data (PANs) is masked in database log columns.
    """
    def __init__(self):
        self.url = getattr(settings, 'PAN_API_URL', 'https://sandbox.cashfree.com/verification/pan')
        self.client_id = getattr(settings, 'PAN_CLIENT_ID', '')
        self.client_secret = getattr(settings, 'PAN_CLIENT_SECRET', '')

    def _mask_payload(self, payload):
        """Safely masks PAN numbers in request/response payloads for logging (Constitution P6)."""
        if not isinstance(payload, dict):
            return payload

        masked = payload.copy()
        if 'pan' in masked:
            masked['pan'] = '***'
        return masked

    def verify_pan(self, pan_number, student):
        """
        POST /verification/pan
        Verifies PAN number and checks name match score against student name.
        """
        headers = {
            "Content-Type": "application/json",
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret
        }
        
        request_payload = {
            "pan": pan_number,
            "name": student.full_name or ""
        }

        # Initialize log object BEFORE network call (api-logging-pattern SKILL.md)
        log = PANApiLog(
            student=student,
            apaar_id=student.apaar_id,
            endpoint="pan_verify",
            request_url=self.url,
            request_headers={
                "Content-Type": "application/json",
                "x-client-id": "***",
                "x-client-secret": "***"
            },
        )

        start = time.monotonic()
        try:
            response = requests.post(self.url, json=request_payload, headers=headers, timeout=15)
            log.http_status = response.status_code

            try:
                body = response.json()
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            log.response_payload = self._mask_payload(body)

            # Success check: status code 200, valid is True
            success = (
                response.status_code == 200 and
                body.get("valid") is True
            )
            log.success = success
            if not success:
                log.error_message = body.get("message") or "PAN Verification failed or returned invalid status"

            return body

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {exc}"
            raise exc

        finally:
            # Measure duration and complete audit save in finally block (Constitution P1)
            log.request_payload = self._mask_payload(request_payload)
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save()
