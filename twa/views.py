import time
import json
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, exceptions

from applications.models import Student
from webhooks.models import WebhookLog
from applications.views import make_json_safe
from webhooks.dispatcher import ABCWebhookDispatcher

class WebhookLoggingAPIView(APIView):
    """
    Base class for inbound TWA webhook views.
    Per Constitution P1, it guarantees exactly one WebhookLog log row is written in a finally block.
    Per Constitution P6, all sensitive data is masked.
    Per §8, it enforces IP whitelisting.
    """
    def dispatch(self, request, *args, **kwargs):
        start_time = time.monotonic()
        source_ip = request.META.get('REMOTE_ADDR')

        # IP Whitelisting verification
        allowed_ips = getattr(settings, 'TWA_WEBHOOK_ALLOWED_IPS', ['127.0.0.1'])
        ip_whitelisted = source_ip in allowed_ips

        # Determine webhook type based on path
        path = request.path
        if 'application-status' in path:
            webhook_type = 'inbound_twa_app_status'
        elif 'kyc-status' in path:
            webhook_type = 'inbound_twa_kyc_status'
        else:
            webhook_type = 'inbound_twa_unknown'

        # Initialize WebhookLog before processing view logic
        log = WebhookLog(
            direction='inbound',
            webhook_type=webhook_type,
            endpoint=path,
            source_ip=source_ip,
            ip_whitelisted=ip_whitelisted,
        )

        response_body = None
        http_status_code = 500
        success = False
        error_msg = None

        try:
            # Enforce whitelisting check
            if not ip_whitelisted:
                # Still log the rejected attempt and return 403 Forbidden
                http_status_code = status.HTTP_403_FORBIDDEN
                response = JsonResponse(
                    {"error": "Forbidden: IP not whitelisted."},
                    status=http_status_code
                )
                log.http_status = http_status_code
                log.success = False
                log.error_message = f"IP {source_ip} is not in the whitelists."
                return response

            # Process request through standard DRF handler
            response = super().dispatch(request, *args, **kwargs)

            http_status_code = response.status_code
            success = 200 <= response.status_code < 300

            if hasattr(response, 'data'):
                response_body = response.data
            else:
                response_body = {'_raw_content': str(response.content)[:1000]}

            log.http_status = http_status_code
            log.success = success
            return response

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            log.success = False
            log.error_message = error_msg
            log.http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
            raise e

        finally:
            # 1. Safely extract and parse request body/payload after view handler runs
            payload = None
            drf_request = getattr(self, 'request', None)
            if drf_request:
                try:
                    payload = drf_request.data
                except Exception:
                    pass

            if not payload:
                try:
                    # In case of early returns (e.g. whitelisting failure), read raw request body
                    if hasattr(request, 'body') and request.body:
                        payload = json.loads(request.body)
                except Exception:
                    pass

            # Mask request payload PII
            masked_payload = None
            if isinstance(payload, dict):
                masked_payload = payload.copy()
                for key in ('pan_number', 'pan', 'otp', 'password', 'token', 'auth_token', 'full_name'):
                    if key in masked_payload:
                        masked_payload[key] = '***'

            log.payload = make_json_safe(masked_payload)

            # Measure duration and complete audit save in finally block (Constitution P1)
            log.duration_ms = int((time.monotonic() - start_time) * 1000)

            # Link log row to Student record
            tracking_id = None
            if isinstance(payload, dict) and 'tracking_id' in payload:
                tracking_id = payload['tracking_id']
            
            if tracking_id:
                log.tracking_id = tracking_id
                try:
                    student = Student.objects.get(tracking_id=tracking_id)
                    log.student = student
                except Student.DoesNotExist:
                    pass
            else:
                log.tracking_id = 'UNKNOWN'

            log.save()


class ApplicationStatusWebhookView(WebhookLoggingAPIView):
    """
    POST /v1/twa/webhook/application-status
    TWA pushes processing status updates for a student.
    """
    def post(self, request, *args, **kwargs):
        tracking_id = request.data.get('tracking_id')
        processing_status = request.data.get('processing_status')
        remarks = request.data.get('remarks')

        if not tracking_id or not processing_status:
            return Response(
                {"error": "tracking_id and processing_status are mandatory."},
                status=status.HTTP_400_BAD_REQUEST
            )

        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Transition status
        # Application statuses: RECEIVED -> PROCESSING -> ISSUED | REJECTED | FAILED
        valid_statuses = dict(Student.APPLICATION_STATUS_CHOICES)
        if processing_status not in valid_statuses:
            return Response(
                {"error": f"Invalid processing_status '{processing_status}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        student.application_status = processing_status
        student.save()

        # Webhook callback forwarding to ABC
        dispatcher = ABCWebhookDispatcher()
        dispatcher.dispatch_application_status_update(student, remarks)

        return Response({
            "tracking_id": student.tracking_id,
            "application_status": student.application_status,
            "message": "Student application status successfully updated from TWA webhook."
        }, status=status.HTTP_200_OK)


class KYCStatusWebhookView(WebhookLoggingAPIView):
    """
    POST /v1/twa/webhook/kyc-status
    TWA pushes kyc status updates for a student.
    """
    def post(self, request, *args, **kwargs):
        tracking_id = request.data.get('tracking_id')
        kyc_status_val = request.data.get('kyc_status')
        remarks = request.data.get('remarks')

        if not tracking_id or not kyc_status_val:
            return Response(
                {"error": "tracking_id and kyc_status are mandatory."},
                status=status.HTTP_400_BAD_REQUEST
            )

        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Transition status
        # KYC statuses: MIN_KYC -> FULL_KYC | FAILED | REJECTED
        valid_statuses = dict(Student.KYC_STATUS_CHOICES)
        if kyc_status_val not in valid_statuses:
            return Response(
                {"error": f"Invalid kyc_status '{kyc_status_val}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        student.kyc_status = kyc_status_val
        student.save()

        # Webhook callback forwarding to ABC
        dispatcher = ABCWebhookDispatcher()
        dispatcher.dispatch_kyc_status_update(student, remarks)

        return Response({
            "tracking_id": student.tracking_id,
            "kyc_status": student.kyc_status,
            "message": "Student KYC status successfully updated from TWA webhook."
        }, status=status.HTTP_200_OK)
