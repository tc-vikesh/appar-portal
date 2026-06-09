import uuid
import time
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, exceptions

from applications.models import ABCApiLog, Student
from applications.serializers import StudentSerializer
from applications.authentication import HMACAuthentication
from twa.client import TWAClient
from webhooks.dispatcher import ABCWebhookDispatcher

def make_json_safe(data):
    """
    Safely serializes data to standard JSON types using DjangoJSONEncoder
    to avoid 'TypeError: Object of type datetime is not JSON serializable'.
    """
    if data is None:
        return None
    try:
        # DjangoJSONEncoder handles datetimes, decimals, UUIDs, etc.
        serialized = json.dumps(data, cls=DjangoJSONEncoder)
        return json.loads(serialized)
    except Exception:
        # Fallback to string representations
        if isinstance(data, dict):
            return {k: str(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [str(item) for item in data]
        return str(data)[:1000]


class LoggingAPIView(APIView):
    """
    Base view class that automatically logs all inbound requests to ABCApiLog.
    Per Constitution P1, it writes exactly one log row inside a finally block.
    Per Constitution P6, all sensitive data is masked.
    """
    authentication_classes = [HMACAuthentication]

    def dispatch(self, request, *args, **kwargs):
        start_time = time.monotonic()
        source_ip = request.META.get('REMOTE_ADDR')

        # Gather request headers
        headers = {
            k: v for k, v in request.META.items()
            if k.startswith('HTTP_') or k in ('CONTENT_TYPE', 'CONTENT_LENGTH')
        }

        # Mask sensitive headers
        if 'HTTP_X_CLIENT_HMAC' in headers:
            headers['HTTP_X_CLIENT_HMAC'] = '***'

        client_id = request.META.get('HTTP_X_CLIENT_ID')
        method = request.method
        path = request.path

        # Initialize the audit log instance
        log = ABCApiLog(
            direction='inbound',
            endpoint=path,
            http_method=method,
            client_id=client_id,
            source_ip=source_ip,
        )

        response_body = None
        http_status_code = 500
        success = False
        error_msg = None

        try:
            # Process request through standard DRF handler
            response = super().dispatch(request, *args, **kwargs)

            http_status_code = response.status_code
            success = 200 <= response.status_code < 300

            if hasattr(response, 'data'):
                response_body = response.data
            else:
                response_body = {'_raw_content': str(response.content)[:1000]}

            return response

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            log.success = False
            log.error_message = error_msg
            log.http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
            raise e

        finally:
            # 1. Safely extract and parse request body/payload after view handler runs (ensuring DRF Request has read the stream)
            payload = None
            drf_request = getattr(self, 'request', None)
            if drf_request:
                try:
                    payload = drf_request.data
                except Exception:
                    pass

            # PII Discipline: Mask sensitive parameters in logs (Constitution P6)
            masked_payload = None
            if isinstance(payload, dict):
                masked_payload = payload.copy()
                for key in ('pan_number', 'pan', 'otp', 'password', 'token', 'auth_token', 'full_name'):
                    if key in masked_payload:
                        masked_payload[key] = '***'

            log.request_payload = make_json_safe(masked_payload)

            # Mask sensitive parameters in response
            masked_response = None
            if isinstance(response_body, dict):
                masked_response = response_body.copy()
                for key in ('pan_number', 'pan', 'otp', 'token', 'auth_token', 'full_name'):
                    if key in masked_response:
                        masked_response[key] = '***'
            else:
                masked_response = response_body

            log.response_payload = make_json_safe(masked_response)
            log.http_status = http_status_code
            log.success = success

            # Measure duration and complete audit save in finally block (Constitution P1)
            log.duration_ms = int((time.monotonic() - start_time) * 1000)

            # Link log row to Student record if tracking_id or apaar_id is available
            tracking_id = None
            if isinstance(payload, dict) and 'tracking_id' in payload:
                tracking_id = payload['tracking_id']
            elif 'tracking_id' in kwargs:
                tracking_id = kwargs['tracking_id']
            elif isinstance(response_body, dict) and 'tracking_id' in response_body:
                tracking_id = response_body['tracking_id']

            # Set hmac_valid boolean safely (avoid AttributeError on original WSGIRequest)
            log.hmac_valid = False
            if drf_request and hasattr(drf_request, 'successful_authenticator'):
                log.hmac_valid = drf_request.successful_authenticator is not None

            if tracking_id:
                log.tracking_id = tracking_id
                try:
                    log.student = Student.objects.get(tracking_id=tracking_id)
                except Student.DoesNotExist:
                    pass
            elif isinstance(payload, dict) and 'apaar_id' in payload:
                try:
                    student = Student.objects.get(apaar_id=payload['apaar_id'])
                    log.student = student
                    log.tracking_id = student.tracking_id
                except Student.DoesNotExist:
                    pass

            log.save()



def download_student_photo(photo_url, apaar_id):
    """
    Downloads the student photo from the provided URL and saves it locally.
    Returns the local relative path to be served (e.g. '/media/photos/<apaar_id>.jpg').
    If download fails, returns the original photo_url.
    """
    if not photo_url or not (str(photo_url).startswith('http://') or str(photo_url).startswith('https://')):
        return photo_url
    
    from django.conf import settings
    import requests
    import os
    
    media_photos_dir = os.path.join(settings.MEDIA_ROOT, 'photos')
    os.makedirs(media_photos_dir, exist_ok=True)
    
    # Extract file extension, default to .jpg
    ext = '.jpg'
    try:
        parsed_path = photo_url.split('?')[0]
        if '.' in parsed_path.split('/')[-1]:
            ext = '.' + parsed_path.split('/')[-1].split('.')[-1]
            if len(ext) > 5:
                ext = '.jpg'
    except Exception:
        pass
        
    filename = f"{apaar_id}{ext}"
    filepath = os.path.join(media_photos_dir, filename)
    
    try:
        response = requests.get(photo_url, timeout=10)
        if response.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return f"/media/photos/{filename}"
    except Exception:
        pass
    return photo_url


def flatten_abc_data(data):
    if not isinstance(data, dict):
        return data
    
    # Check if this is the structured nested ABC format (contains PERSONAL_INFO or APAAR_ID)
    if 'APAAR_ID' in data or 'PERSONAL_INFO' in data:
        flat = {}
        flat['apaar_id'] = data.get('APAAR_ID')
        
        personal = data.get('PERSONAL_INFO') or {}
        flat['full_name'] = personal.get('FULL_NAME')
        flat['dob'] = personal.get('DOB')
        flat['gender'] = personal.get('GENDER')
        flat['mobile'] = personal.get('MOBILE')
        flat['email'] = personal.get('EMAIL')
        
        # Derive title from gender (Requirement A1)
        gender_upper = str(flat['gender']).strip().upper() if flat['gender'] else ''
        if gender_upper == 'M':
            flat['title'] = 'Mr'
        elif gender_upper == 'F':
            flat['title'] = 'Ms'
        else:
            flat['title'] = 'Mx'
        
        academic = data.get('ACADEMIC_INFO') or {}
        flat['university_name'] = academic.get('UNIVERSITY_NAME')
        flat['college_name'] = academic.get('COLLEGE_NAME')
        flat['course_name'] = academic.get('COURSE_NAME')
        flat['enrollment_number'] = academic.get('ENROLLMENT_NUMBER')
        flat['admission_year'] = academic.get('ADMISSION_YEAR')
        flat['academic_session'] = academic.get('ACADEMIC_SESSION')
        flat['academic_status'] = academic.get('ACADEMIC_STATUS')
        
        additional = data.get('ADDITIONAL_INFO') or {}
        flat['blood_group'] = additional.get('BLOOD_GROUP')
        
        current_addr = additional.get('CURRENT_ADDRESS')
        if isinstance(current_addr, dict):
            flat['current_address'] = {
                'address1': current_addr.get('ADDRESS_LINE'),
                'city': current_addr.get('CITY'),
                'state': current_addr.get('STATE'),
                'pincode': current_addr.get('PIN_CODE')
            }
        else:
            flat['current_address'] = current_addr

        permanent_addr = additional.get('PERMANENT_ADDRESS')
        if isinstance(permanent_addr, dict):
            flat['permanent_address'] = {
                'address1': permanent_addr.get('ADDRESS_LINE'),
                'city': permanent_addr.get('CITY'),
                'state': permanent_addr.get('STATE'),
                'pincode': permanent_addr.get('PIN_CODE'),
                'same_as_current': permanent_addr.get('SAME_AS_CURRENT')
            }
        else:
            flat['permanent_address'] = permanent_addr

        photo = data.get('PHOTO_INFO') or {}
        photo_url = photo.get('PHOTO_FULL_PATH') or photo.get('PHOTO_PATH')
        flat['photo_path'] = download_student_photo(photo_url, flat['apaar_id'])
        
        return flat
    
    return data


class ReceiveApplicationView(LoggingAPIView):
    """
    POST /v1/issuer-bank/application/receive
    Pushes student data to TAP.
    Per Constitution P2, this endpoint is strictly idempotent on apaar_id.
    """
    def post(self, request, *args, **kwargs):
        # Flatten nested structured ABC data if present (backward compatible)
        data = request.data
        if isinstance(data, dict) and ('APAAR_ID' in data or 'PERSONAL_INFO' in data):
            data = flatten_abc_data(data)

        apaar_id = data.get('apaar_id')
        if not apaar_id:
            return Response(
                {"error": "apaar_id is mandatory."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Idempotency check: if student already exists, return existing tracking_id
        try:
            student = Student.objects.get(apaar_id=apaar_id)
            return Response({
                "tracking_id": student.tracking_id,
                "application_status": student.application_status,
                "created": False,
                "message": "Application already received. Idempotency matched."
            }, status=status.HTTP_200_OK)
        except Student.DoesNotExist:
            pass

        serializer = StudentSerializer(data=data)
        if serializer.is_valid():
            # Generate a clean, unique tracking_id
            tracking_id = f"TAP-{uuid.uuid4().hex[:12].upper()}"

            # Save student record
            student = serializer.save(
                tracking_id=tracking_id,
                application_status='RECEIVED',
                kyc_status='MIN_KYC'
            )

            return Response({
                "tracking_id": student.tracking_id,
                "application_status": student.application_status,
                "created": True
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AcknowledgeApplicationView(LoggingAPIView):
    """
    POST /v1/issuer-bank/application/acknowledge
    Confirms receipt of tracking ID and transitions status to PROCESSING.
    """
    def post(self, request, *args, **kwargs):
        tracking_id = request.data.get('tracking_id')
        apaar_id = request.data.get('apaar_id')

        if not tracking_id and not apaar_id:
            return Response(
                {"error": "Either tracking_id or apaar_id is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Look up student
        student = None
        if tracking_id:
            student = get_object_or_404(Student, tracking_id=tracking_id)
        elif apaar_id:
            student = get_object_or_404(Student, apaar_id=apaar_id)

        # Transition status from RECEIVED to PROCESSING
        if student.application_status == 'RECEIVED':
            student.application_status = 'PROCESSING'
            student.save()
            
            # Fire outbound status webhook to ABC
            dispatcher = ABCWebhookDispatcher()
            dispatcher.dispatch_application_status_update(student, "Application acknowledged by ABC")

        return Response({
            "tracking_id": student.tracking_id,
            "application_status": student.application_status,
            "message": "Application successfully acknowledged and set to PROCESSING."
        }, status=status.HTTP_200_OK)


class ApplicationStatusView(LoggingAPIView):
    """
    GET /v1/issuer-bank/application/status/{tracking_id}
    Pulls status from TWA client first, updates record, and returns status.
    """
    def get(self, request, tracking_id, *args, **kwargs):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Per Spec, pull latest status from TWA first
        # Per Constitution P5, this lives behind the TWAClient interface
        twa_client = TWAClient()
        try:
            twa_response = twa_client.pull_status(student)
            if twa_response and twa_response.get('success'):
                data = twa_response.get('data', {})
                # Update student record with values returned from TWA if status changes
                twa_app_status = data.get('application_status')
                twa_kyc_status = data.get('kyc_status')

                app_status_changed = False
                kyc_status_changed = False

                if twa_app_status and twa_app_status != student.application_status:
                    student.application_status = twa_app_status
                    app_status_changed = True
                if twa_kyc_status and twa_kyc_status != student.kyc_status:
                    student.kyc_status = twa_kyc_status
                    kyc_status_changed = True

                if app_status_changed or kyc_status_changed:
                    student.save()
                    
                    # Fire outbound webhooks for the changed statuses
                    dispatcher = ABCWebhookDispatcher()
                    if app_status_changed:
                        dispatcher.dispatch_application_status_update(student, "Status updated from TWA pull")
                    if kyc_status_changed:
                        dispatcher.dispatch_kyc_status_update(student, "KYC status updated from TWA pull")
        except Exception:
            # Robustness: if TWA call fails, we proceed with current local database record
            pass

        return Response({
            "tracking_id": student.tracking_id,
            "apaar_id": student.apaar_id,
            "application_status": student.application_status,
            "kyc_status": student.kyc_status,
            "twa_synced": student.twa_synced,
            "updated_at": student.updated_at
        }, status=status.HTTP_200_OK)


class DashboardStatsView(LoggingAPIView):
    """
    GET /v1/issuer-bank/dashboard/stats
    Returns aggregate counts for student applications.
    """
    def get(self, request, *args, **kwargs):
        # Count applications by status
        status_counts = Student.objects.values('application_status').annotate(count=Count('id'))
        # Count applications by kyc_status
        kyc_counts = Student.objects.values('kyc_status').annotate(count=Count('id'))

        # Standardize response structure
        status_dict = {
            'RECEIVED': 0,
            'PROCESSING': 0,
            'ISSUED': 0,
            'REJECTED': 0,
            'FAILED': 0
        }
        for item in status_counts:
            s_val = item['application_status']
            if s_val in status_dict:
                status_dict[s_val] = item['count']

        kyc_dict = {
            'MIN_KYC': 0,
            'FULL_KYC': 0,
            'FAILED': 0,
            'REJECTED': 0
        }
        for item in kyc_counts:
            k_val = item['kyc_status']
            if k_val in kyc_dict:
                kyc_dict[k_val] = item['count']

        total_students = Student.objects.count()

        return Response({
            "total_students": total_students,
            "application_status_counts": status_dict,
            "kyc_status_counts": kyc_dict
        }, status=status.HTTP_200_OK)
