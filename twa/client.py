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

        # Generate presigned URL for photo if it's stored in S3
        photo_url = student.photo_path or ""
        if photo_url and not photo_url.startswith('http') and not photo_url.startswith('/media/'):
            try:
                import boto3
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=settings.AWS_S3_REGION_NAME
                )
                photo_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                        'Key': photo_url
                    },
                    ExpiresIn=3600  # 1 hour expiry
                )
            except Exception as e:
                print(f"Error generating presigned URL: {e}")

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
            "photoUrl": photo_url,
            "cards": [
                {
                    "kitNumber": student.m2p_kit_no or "",
                    "cardType": "VIRTUAL",
                    "networkType": "RUPAY"
                }
            ]
        }

        from twa.crypto import encrypt_twa_payload, decrypt_twa_payload
        from applications.views import make_json_safe

        # Encrypt Payload
        encrypted_payload = None
        try:
            encrypted_payload = encrypt_twa_payload(payload)
            request_body = encrypted_payload
        except Exception as enc_err:
            # Fallback to plain payload if encryption is not configured
            request_body = payload

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
            response = requests.post(url, json=request_body, headers=headers, timeout=15)
            log.http_status = response.status_code

            try:
                raw_body = response.json()
            except ValueError:
                raw_body = {"_raw_body": response.text[:1000]}

            # Check if response body is encrypted
            body = raw_body
            if isinstance(raw_body, dict) and 'encryptedData' in raw_body:
                log.encrypted_response_payload = make_json_safe(raw_body)
                try:
                    decrypted_resp = decrypt_twa_payload(raw_body['encryptedData'])
                    body = decrypted_resp
                except Exception:
                    body = raw_body

            log.response_payload = make_json_safe(body)

            success = 200 <= response.status_code < 300
            log.success = success
            if not success:
                log.error_message = (body.get("error") or body.get("message") or "Non-success TWA response") if isinstance(body, dict) else "Non-success TWA response"

            return body

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {exc}"
            raise exc

        finally:
            log.request_payload = make_json_safe(payload)
            if encrypted_payload:
                log.encrypted_request_payload = make_json_safe(encrypted_payload)
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save() # ALWAYS save log row


