import time
import uuid
import requests
from django.conf import settings
from applications.models import AadhaarApiLog

def mask_aadhaar_in_payload(payload):
    """
    Recursively masks any Aadhaar number in request/response payloads
    so that only the last 4 digits are stored in logs.
    """
    if not isinstance(payload, dict):
        return payload
    sanitized = {}
    for k, v in payload.items():
        if k in ("aadhaar_number", "aadhaar_no", "documentNo") and isinstance(v, str):
            sanitized[k] = v[-4:] if len(v) >= 4 else v
        elif isinstance(v, dict):
            sanitized[k] = mask_aadhaar_in_payload(v)
        elif isinstance(v, list):
            sanitized[k] = [mask_aadhaar_in_payload(item) if isinstance(item, dict) else item for item in v]
        else:
            sanitized[k] = v
    return sanitized


def new_digilocker_verification_id():
    """Cashfree verification_id: unique, max 50 chars, [A-Za-z0-9._-]."""
    return f"TAP-{uuid.uuid4().hex}"


class AadhaarClient:
    """
    Client for Cashfree DigiLocker (Aadhaar) and Name Match APIs.
    Per P1, all outbound calls are logged in AadhaarApiLog inside a finally block.
    Only the last 4 digits of Aadhaar are ever stored in logs.

    DigiLocker flow:
      1. digilocker_create_url   POST /digilocker
      2. (student consents on DigiLocker and is redirected back to TAP)
      3. digilocker_get_status   GET  /digilocker?verification_id=
      4. digilocker_get_document GET  /digilocker/document/AADHAAR?verification_id=
    """
    def __init__(self):
        self.base_url = getattr(settings, 'CASHFREE_BASE_URL', 'https://sandbox.cashfree.com/verification')
        self.client_id = getattr(settings, 'CASHFREE_CLIENT_ID', '')
        self.client_secret = getattr(settings, 'CASHFREE_CLIENT_SECRET', '')

    def _get_headers(self):
        return {
            "Content-Type": "application/json",
            "X-Client-Id": self.client_id,
            "X-Client-Secret": self.client_secret
        }

    def _get_masked_headers(self, headers):
        masked = dict(headers)
        if "X-Client-Secret" in masked and masked["X-Client-Secret"]:
            masked["X-Client-Secret"] = "******"
        return masked

    def _call(self, endpoint, method, url, student, payload, is_success, default_error, mock_body=None):
        """
        Performs one Cashfree call and writes exactly one AadhaarApiLog row.
        `payload` is sent as JSON body for POST and as query params for GET.
        `mock_body`, when not None and MOCK_CASHFREE_APIS is on, is returned instead of calling out.
        """
        headers = self._get_headers()
        log = AadhaarApiLog(
            student=student,
            apaar_id=student.apaar_id,
            endpoint=endpoint,
            request_url=url,
            request_headers=self._get_masked_headers(headers),
        )

        start = time.monotonic()
        try:
            if mock_body is not None and getattr(settings, 'MOCK_CASHFREE_APIS', False):
                log.http_status = 200
                log.response_payload = mask_aadhaar_in_payload(mock_body)
                log.success = True
                return mock_body

            if method == 'GET':
                response = requests.get(url, params=payload, headers=headers, timeout=15)
            else:
                response = requests.post(url, json=payload, headers=headers, timeout=15)
            log.http_status = response.status_code

            try:
                body = response.json()
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            log.response_payload = mask_aadhaar_in_payload(body)
            success = response.status_code == 200 and is_success(body)
            log.success = success
            if not success:
                log.error_message = (body.get("message") if isinstance(body, dict) else None) or default_error

            return body

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {exc}"
            raise exc

        finally:
            log.request_payload = mask_aadhaar_in_payload(payload)
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save()

    def digilocker_create_url(self, verification_id, redirect_url, student):
        """
        POST /digilocker
        Creates the DigiLocker consent URL (valid ~10 minutes) for an Aadhaar document request.
        """
        payload = {
            "verification_id": verification_id,
            "document_requested": ["AADHAAR"],
            "redirect_url": redirect_url,
            "user_flow": getattr(settings, 'DIGILOCKER_USER_FLOW', 'signup'),
        }
        purpose = getattr(settings, 'DIGILOCKER_PURPOSE', '')
        if purpose:
            payload["purpose"] = purpose
        company_name = getattr(settings, 'DIGILOCKER_COMPANY_NAME', '')
        if company_name:
            payload["company_name"] = company_name

        mock_body = {
            "verification_id": verification_id,
            "reference_id": 1000001,
            # In mock mode the "DigiLocker" consent page is TAP's own callback.
            "url": redirect_url,
            "status": "PENDING",
            "document_requested": ["AADHAAR"],
            "redirect_url": redirect_url,
            "user_flow": payload["user_flow"],
        }
        return self._call(
            "digilocker_create_url", "POST", f"{self.base_url.rstrip('/')}/digilocker",
            student, payload,
            is_success=lambda b: isinstance(b, dict) and bool(b.get("url")),
            default_error="Failed to create DigiLocker URL",
            mock_body=mock_body,
        )

    def digilocker_get_status(self, verification_id, student):
        """
        GET /digilocker?verification_id=...
        Returns status: PENDING, AUTHENTICATED, EXPIRED or CONSENT_DENIED.
        """
        payload = {"verification_id": verification_id}
        mock_body = {"verification_id": verification_id, "status": "AUTHENTICATED"}
        return self._call(
            "digilocker_status", "GET", f"{self.base_url.rstrip('/')}/digilocker",
            student, payload,
            is_success=lambda b: isinstance(b, dict) and bool(b.get("status")),
            default_error="Failed to fetch DigiLocker status",
            mock_body=mock_body,
        )

    def digilocker_get_document(self, verification_id, student):
        """
        GET /digilocker/document/AADHAAR?verification_id=...
        Returns the user's Aadhaar demographics (name, dob, gender, address, masked aadhaar_number).
        """
        payload = {"verification_id": verification_id}
        mock_body = {
            "name": student.full_name,
            "dob": str(student.dob) if student.dob else "",
            "gender": student.gender or "",
            "aadhaar_number": "XXXXXXXX9012",
        }
        return self._call(
            "digilocker_document", "GET", f"{self.base_url.rstrip('/')}/digilocker/document/AADHAAR",
            student, payload,
            is_success=lambda b: isinstance(b, dict) and bool(b.get("name") or (b.get("data") or {}).get("name")),
            default_error="Failed to fetch Aadhaar document from DigiLocker",
            mock_body=mock_body,
        )

    def name_match(self, name_1, name_2, student):
        """
        POST /name-match
        Matches the ABC student full name against the Aadhaar-returned name.
        """
        verification_id = f"CF-MATCH-{uuid.uuid4().hex[:12].upper()}"
        payload = {
            "verification_id": verification_id,
            "name_1": name_1,
            "name_2": name_2
        }
        mock_body = {"status": "SUCCESS", "score": 1.0, "data": {"score": 1.0}} if name_2 == student.full_name else None
        return self._call(
            "name_match", "POST", f"{self.base_url.rstrip('/')}/name-match",
            student, payload,
            is_success=lambda b: isinstance(b, dict) and b.get("status") == "SUCCESS",
            default_error="Name match request failed",
            mock_body=mock_body,
        )
