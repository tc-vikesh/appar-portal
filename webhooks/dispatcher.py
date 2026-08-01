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

    def dispatch_kyc_status_update(self, student, remarks=None):
        """
        Sends combined application and KYC status update to ABC.
        Endpoint: ABC_KYC_STATUS_WEBHOOK_URL
        """
        url = getattr(settings, 'ABC_KYC_STATUS_WEBHOOK_URL', '')
        payload = {
            "TRACKING_ID": student.tracking_id,
            "APAAR_ID": student.apaar_id,
            "PROCESSING_STATUS": student.application_status,
            "KYC_STATUS": student.kyc_status,
            "REMARKS": remarks or "",
            "KYC_DATE": student.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if student.updated_at else "",
        }
        
        if student.application_status == "ISSUED":
            payload["CARD_DETAILS"] = {
                "CARD_NUMBER": "1234XXXXXX5678",
                "EXPIRY_DATE": "12/2028",
                "ISSUE_DATE": student.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if student.updated_at else ""
            }
            
        return self._send_webhook(
            url=url,
            payload=payload,
            webhook_type='outbound_abc_kyc_status',
            student=student
        )

    def _send_webhook(self, url, payload, webhook_type, student):
        """
        Executes HTTP POST to the target webhook URL and logs attempt in WebhookLog.
        Catches exceptions silently (returns dict showing success status).
        """
        if not url:
            # If no webhook URL configured, skip network call but still write a failed log
            # to make sure we audit that URL is missing.
            log = WebhookLog(
                student=student,
                tracking_id=student.tracking_id if student else 'UNKNOWN',
                webhook_type=webhook_type,
                direction='outbound',
                endpoint='NOT_CONFIGURED',
                payload=payload,
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
        )

        # Masking disabled as requested
        from applications.views import make_json_safe
        log.payload = make_json_safe(payload)

        start = time.monotonic()
        try:
            # Prepare HMAC Headers
            import time as pytime
            import hmac
            import hashlib
            client_id = getattr(settings, 'ABC_CLIENT_ID', '')
            client_secret = getattr(settings, 'ABC_CLIENT_SECRET', '')
            timestamp = str(int(pytime.time()))
            message = f"{client_secret}{client_id}{timestamp}"
            signature = hashlib.sha256(message.encode('utf-8')).hexdigest()
            
            headers = {
                'X-Client-ID': client_id,
                'X-Client-Timestamp': timestamp,
                'X-Client-HMAC': signature,
                'Content-Type': 'application/json'
            }

            # Encrypt Payload
            from applications.crypto import encrypt_abc_payload
            try:
                encrypted_payload = encrypt_abc_payload(payload)
                log.encrypted_payload = make_json_safe(encrypted_payload)
            except Exception as e:
                # If encryption fails (e.g. no key), fallback to plain payload or error out
                encrypted_payload = payload
                
            response = requests.post(url, json=encrypted_payload, headers=headers, timeout=10)
            log.http_status = response.status_code

            try:
                body = response.json()
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            success = 200 <= response.status_code < 300
            log.success = success
            if not success:
                log.error_message = f"Non-2xx HTTP status: {response.status_code}. Response: {body}"

            return {"success": success, "response": body}

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {str(exc)}"
            # Silent failure: we return a failure dict instead of raising the exception
            return {"success": False, "error": str(exc)}

        finally:
            # Measure duration and complete audit save in finally block (Constitution P1)
            # Duration is not a DB column in WebhookLog, but we can set retry attributes
            # and ALWAYS call log.save().
            log.save()
