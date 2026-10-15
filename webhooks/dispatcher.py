import time
import requests
from django.conf import settings
from django.utils import timezone
from webhooks.models import WebhookLog


class ABCWebhookDispatcher:
    """
    Dispatcher for firing outbound webhook updates from TAP to ABC.
    Per Skill: API Logging Pattern, all outbound calls are audited in WebhookLog
    inside a finally block, ensuring exactly one log row is written even on failure.
    Per Sprint 5 requirements, all failures are caught silently (non-blocking).
    """

    DEFAULT_WEBHOOK_URL = 'https://api-dev2abc.abcnad.in/api/v1/webhook/kyc-status'

    def dispatch_status_update(self, student, remarks=None):
        """
        Sends unified application processing status and KYC status update to ABC.
        Endpoint: ABC_KYC_STATUS_WEBHOOK_URL (or ABC_STATUS_UPDATE_WEBHOOK_URL / fallback).
        """
        url = getattr(settings, 'ABC_KYC_STATUS_WEBHOOK_URL', None)
        if url is None:
            url = getattr(settings, 'ABC_STATUS_UPDATE_WEBHOOK_URL', None)
        if url is None:
            url = getattr(settings, 'ABC_APP_STATUS_WEBHOOK_URL', None)
        if url is None:
            url = self.DEFAULT_WEBHOOK_URL

        kyc_date_str = ""
        if student and student.updated_at:
            kyc_date_str = student.updated_at.strftime("%d/%m/%Y %H:%M:%S")
        else:
            kyc_date_str = timezone.now().strftime("%d/%m/%Y %H:%M:%S")

        payload = {
            "TRACKING_ID": student.tracking_id if student else "",
            "APAAR_ID": student.apaar_id if student else "",
            "PROCESSING_STATUS": (student.application_status or "").upper() if student else "",
            "KYC_STATUS": (student.kyc_status or "").upper() if student else "",
            "REMARKS": remarks or "",
            "KYC_DATE": kyc_date_str,
        }

        return self._send_webhook(
            url=url,
            payload=payload,
            webhook_type='outbound_abc_kyc_status',
            student=student
        )

    def dispatch_application_status_update(self, student, remarks=None):
        """
        Alias for dispatch_status_update to maintain backward compatibility.
        """
        return self.dispatch_status_update(student, remarks=remarks)

    def dispatch_kyc_status_update(self, student, remarks=None):
        """
        Alias for dispatch_status_update to maintain backward compatibility.
        """
        return self.dispatch_status_update(student, remarks=remarks)

    @staticmethod
    def generate_webhook_hmac_headers(client_id, client_secret, timestamp=None):
        """
        Generates HMAC authentication headers specifically for ABC outbound webhooks.
        Per ABC PHP reference:
            $bodyMessage = $bankClientId . "\n" . $bodyTs;
            $bodyHmac = hash_hmac('sha256', $bodyMessage, $bankClientSecret);
        """
        import time as pytime
        import hmac
        import hashlib
        ts = str(int(pytime.time())) if timestamp is None else str(timestamp)
        msg_bytes = f"{client_id}\n{ts}".encode('utf-8')
        sec_bytes = client_secret.encode('utf-8')
        signature = hmac.new(sec_bytes, msg_bytes, hashlib.sha256).hexdigest()
        return {
            'X-Client-ID': client_id,
            'X-Client-Timestamp': ts,
            'X-Client-HMAC': signature,
            'Content-Type': 'application/json'
        }

    def _send_webhook(self, url, payload, webhook_type, student):
        """
        Executes HTTP POST to the target webhook URL and logs attempt in WebhookLog.
        Catches exceptions silently (returns dict showing success status).
        """
        from applications.views import make_json_safe

        if not url:
            log = WebhookLog(
                student=student,
                tracking_id=student.tracking_id if student else 'UNKNOWN',
                webhook_type=webhook_type,
                direction='outbound',
                endpoint='NOT_CONFIGURED',
                payload=make_json_safe(payload),
                success=False,
                error_message="Webhook URL is not configured in settings."
            )
            log.save()
            return {"success": False, "error": "URL not configured"}

        # Initialize WebhookLog before execution
        log = WebhookLog(
            student=student,
            tracking_id=student.tracking_id if student else 'UNKNOWN',
            webhook_type=webhook_type,
            direction='outbound',
            endpoint=url,
            payload=make_json_safe(payload),
        )

        # Pre-flight check for credentials
        client_id = getattr(settings, 'ABC_CLIENT_ID', '')
        client_secret = getattr(settings, 'ABC_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            log.success = False
            log.error_message = "Missing ABC_CLIENT_ID or ABC_CLIENT_SECRET in settings."
            log.save()
            return {"success": False, "error": "Missing client credentials"}

        start = time.monotonic()
        try:
            # Prepare HMAC Headers specifically for ABC outbound webhook
            headers = self.generate_webhook_hmac_headers(client_id, client_secret)
            log.headers = make_json_safe(headers)

            # Encrypt Payload using AES-256-GCM
            from applications.crypto import encrypt_abc_payload, decrypt_abc_payload
            try:
                encrypted_payload = encrypt_abc_payload(payload)
                log.encrypted_payload = make_json_safe(encrypted_payload)
            except Exception as enc_err:
                log.success = False
                log.error_message = f"Encryption failed: {str(enc_err)}"
                log.save()
                return {"success": False, "error": f"Encryption failed: {str(enc_err)}"}

            response = requests.post(url, json=encrypted_payload, headers=headers, timeout=10)
            log.http_status = response.status_code

            try:
                body = response.json()
                # If ABC returns encrypted response, decrypt it for audit logging
                if isinstance(body, dict) and 'encryptedData' in body:
                    try:
                        body = decrypt_abc_payload(body['encryptedData'])
                    except Exception:
                        pass
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            success = 200 <= response.status_code < 300
            if success and isinstance(body, dict):
                if body.get("status") in ("error", "failed", "failure") or body.get("success") is False:
                    success = False

            log.success = success
            if success:
                log.delivered_at = timezone.now()
            else:
                log.error_message = f"HTTP status: {response.status_code}. Response: {body}"

            return {"success": success, "response": body}

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {str(exc)}"
            return {"success": False, "error": str(exc)}

        finally:
            # Complete audit save in finally block (Constitution P1)
            log.save()
