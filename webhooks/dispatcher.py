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

    def dispatch_application_status_update(self, student, remarks=None):
        """
        Sends application status update to ABC.
        Endpoint: ABC_STATUS_UPDATE_WEBHOOK_URL
        """
        url = getattr(settings, 'ABC_STATUS_UPDATE_WEBHOOK_URL', '')
        payload = {
            "tracking_id": student.tracking_id,
            "apaar_id": student.apaar_id,
            "application_status": student.application_status,
            "remarks": remarks or ""
        }
        return self._send_webhook(
            url=url,
            payload=payload,
            webhook_type='outbound_abc_app_status',
            student=student
        )

    def dispatch_kyc_status_update(self, student, remarks=None):
        """
        Sends KYC status update to ABC.
        Endpoint: ABC_KYC_STATUS_WEBHOOK_URL
        """
        url = getattr(settings, 'ABC_KYC_STATUS_WEBHOOK_URL', '')
        payload = {
            "tracking_id": student.tracking_id,
            "apaar_id": student.apaar_id,
            "kyc_status": student.kyc_status,
            "remarks": remarks or ""
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
            # Call ABC webhook - no authentication needed as per Sprint 5 specs (no-auth)
            # Timeout is kept short to not hang application requests
            response = requests.post(url, json=payload, timeout=10)
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
