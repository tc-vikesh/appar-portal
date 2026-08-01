import time
import requests
from django.conf import settings
from twa.models import TWAApiLog

class TWAClient:
    """
    Client for interacting with the Transcorp Web App (TWA).
    Per Constitution P5, all TWA calls live behind this single interface.
    Per Constitution P1, all outbound calls are logged in TWAApiLog inside a finally block.
    """
    def pull_status(self, student):
        """
        Pulls latest status for the student from TWA.
        Currently mocked until TWA shares the status pull URL.
        """
        url = getattr(settings, 'TWA_STATUS_PULL_URL', 'MOCK_TWA_STATUS_PULL_URL')
        endpoint = 'status_pull'
        payload = {
            'apaar_id': student.apaar_id,
            'tracking_id': student.tracking_id
        }

        # Initialize log row before the call (api-logging-pattern SKILL.md)
        log = TWAApiLog(
            student=student,
            apaar_id=student.apaar_id,
            tracking_id=student.tracking_id,
            endpoint=endpoint,
            request_url=url,
            request_payload=payload,
        )

        start = time.monotonic()
        try:
            # Mock network latency
            time.sleep(0.05)

            # Mock TWA pull success response
            mock_response = {
                "success": True,
                "data": {
                    "tracking_id": student.tracking_id,
                    "application_status": student.application_status,
                    "kyc_status": student.kyc_status,
                    "remarks": "Sync check successful (Mocked TWA pull)."
                }
            }

            log.http_status = 200
            log.response_payload = mock_response
            log.success = True
            return mock_response

        except Exception as e:
            log.success = False
            log.error_message = f"{type(e).__name__}: {str(e)}"
            raise e
        finally:
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save() # ALWAYS save log row

    def _mask_payload(self, payload):
        """Returns the payload unmasked (masking disabled as requested)."""
        return payload

    def sync_onboard(self, student):
        """
        POST https://api.stage.transcorpint.com/user/external/onboard
        Syncs student onboarding data to TWA after successful MIN KYC.
        """
        url = getattr(settings, 'TWA_SYNC_URL', 'https://api.stage.transcorpint.com/user/external/onboard')
        endpoint = 'sync_onboard'
        token = getattr(settings, 'TWA_AUTH_TOKEN', '')

        headers = {
            'authToken': token,
            'Content-Type': 'application/json'
        }

        # Parse full_name into first and last name blocks
        full_name = student.full_name or ""
        name_parts = full_name.split()
        first_name = name_parts[0] if name_parts else "Student"
        last_name = name_parts[-1] if len(name_parts) > 1 else ""

        # Safe address resolution using model properties
        address1 = student.permanent_address_line or "Address Line 1"
        perm_dict = student.permanent_address or {}
        address2 = perm_dict.get('address2') or perm_dict.get('ADDRESS_LINE2') or perm_dict.get('address_line2') or ""
        city = student.permanent_address_city or "Mumbai"
        state = student.permanent_address_state or "Maharashtra"
        pincode = student.permanent_address_pincode or "400001"

        # Kit number parsing is no longer needed since kitNumber is passed as a string directly

        # Safely extract last 4 digits of Aadhaar
        aadhaar_last_4 = ""
        if student.aadhaar_number and len(student.aadhaar_number) >= 4:
            aadhaar_last_4 = student.aadhaar_number[-4:]
        elif student.aadhaar_number:
            aadhaar_last_4 = student.aadhaar_number

        payload = {
            "firstName": first_name,
            "lastName": last_name,
            "email": student.email,
            "dob": str(student.dob) if student.dob else "2000-01-01",
            "gender": student.gender or "M",
            "identifier": "+91" + student.mobile,
            "status": "ACTIVATED",
            "accountIdentifierType": "phone",
            "programName": "TCAPAAR",
            "entityId": student.m2p_entity_id or student.apaar_id,
            "vcipToken": student.m2p_token or "",
            "apaarId": student.apaar_id,
            "permanentAddress": {
                "address1": address1,
                "address2": address2,
                "city": city,
                "state": str(state).upper(),
                "country": "India",
                "pincode": str(pincode)
            },
            "kycStatus": student.kyc_status,
            "idType": "AADHAAR",
            "idValue": aadhaar_last_4,
            "aadhaarInfo": {
                "aadhaar_number": aadhaar_last_4,
                "full_name": (student.full_name or "").upper()
            },
            "cards": [
                {
                    "kitNumber": student.m2p_kit_no or "",
                    "cardType": "VIRTUAL",
                    "networkType": "RUPAY"
                }
            ]
        }

        # Initialize log row BEFORE network call
        log = TWAApiLog(
            student=student,
            apaar_id=student.apaar_id,
            tracking_id=student.tracking_id,
            endpoint=endpoint,
            request_url=url,
        )

        start = time.monotonic()
        try:
            # PII discipline: mask auth token and sensitive payloads in logging
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            log.http_status = response.status_code

            try:
                body = response.json()
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            log.response_payload = self._mask_payload(body)

            success = 200 <= response.status_code < 300
            log.success = success
            if not success:
                log.error_message = body.get("error") or body.get("message") or "Non-success TWA response"

            return body

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {exc}"
            raise exc

        finally:
            log.request_payload = self._mask_payload(payload)
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save() # ALWAYS save log row

