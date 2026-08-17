import hashlib
import time
import json
import uuid
from django.test import TestCase
from django.urls import reverse
from django.conf import settings
from rest_framework import status

from applications.models import Student, ABCApiLog
from twa.models import TWAApiLog


class TAPTestCase(TestCase):
    def setUp(self):
        # Configure standard test variables in settings
        self.client_id = "test_abc_client_id"
        self.client_secret = "test_abc_client_secret"
        settings.ABC_CLIENT_ID = self.client_id
        settings.ABC_CLIENT_SECRET = self.client_secret
        
        import base64
        import os
        self.encryption_key = base64.b64encode(os.urandom(32)).decode('utf-8')
        settings.ABC_ENCRYPTION_KEY = self.encryption_key

        # Valid payload representing standard ABC student data push
        self.student_data = {
            "apaar_id": "APAAR-12345-67890",
            "full_name": "Vikesh Sharma",
            "dob": "2000-01-01",
            "gender": "M",
            "mobile": "9876543210",
            "email": "vikesh@example.com",
            "university_name": "Delhi University",
            "college_name": "Stephen's College",
            "course_name": "B.Sc. Computer Science",
            "enrollment_number": "ENR12345",
            "admission_year": 2021,
            "academic_session": "2021-2024",
            "academic_status": "Active",
            "blood_group": "O+",
            "current_address": {
                "line": "123 North Ave",
                "city": "Delhi",
                "pincode": "110001"
            },
            "permanent_address": {
                "line": "456 South Ave",
                "city": "Jaipur",
                "pincode": "302001"
            },
            "photo_path": "/photos/vikesh.jpg",
            "APPLICATION_REFERENCE_NUMBER": "TAP-123456789"
        }

    def _generate_hmac_headers(self, timestamp=None, secret=None, client_id=None):
        """Helper to generate correct HMAC auth headers."""
        ts = str(timestamp or time.time())
        sec = secret or self.client_secret
        cid = client_id or self.client_id

        # Calculate signature: SHA256(secret + id + timestamp)
        message = f"{sec}{cid}{ts}"
        signature = hashlib.sha256(message.encode('utf-8')).hexdigest()

        return {
            "HTTP_X_CLIENT_ID": cid,
            "HTTP_X_CLIENT_TIMESTAMP": ts,
            "HTTP_X_CLIENT_HMAC": signature
        }

    def _encrypt(self, payload):
        from applications.crypto import encrypt_abc_payload
        return encrypt_abc_payload(payload)

    def _decrypt(self, response_data):
        from applications.crypto import decrypt_abc_payload
        if isinstance(response_data, dict) and "encryptedData" in response_data:
            return decrypt_abc_payload(response_data["encryptedData"])
        return response_data

    # 1. HMAC Authentication Tests
    def test_hmac_authentication_success(self):
        """Test accessing endpoint with completely valid HMAC headers."""
        headers = self._generate_hmac_headers()
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = self._decrypt(response.json())
        self.assertEqual(data.get("status"), "success")

    def test_hmac_authentication_missing_headers(self):
        """Test accessing endpoint with missing HMAC headers (returns 403 / AuthenticationFailed)."""
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Confirm exactly one audit log row is created even on auth failure (Constitution P1)
        log = ABCApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertFalse(log.success)
        self.assertFalse(log.hmac_valid)

    def test_hmac_authentication_invalid_client(self):
        """Test accessing endpoint with incorrect client ID."""
        headers = self._generate_hmac_headers(client_id="bad_client")
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_hmac_authentication_expired_timestamp(self):
        """Test accessing endpoint with expired timestamp (tolerance window limit)."""
        expired_ts = time.time() - 700 # greater than 600s window limit
        headers = self._generate_hmac_headers(timestamp=expired_ts)
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_hmac_authentication_invalid_signature(self):
        """Test accessing endpoint with correct headers but invalid signature."""
        headers = self._generate_hmac_headers()
        headers["HTTP_X_CLIENT_HMAC"] = "invalid_signature_hex"
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # 2. Receive Application API Tests (Validation & Idempotency & PENDING KYC status)
    def test_receive_application_validation_rules(self):
        """Test strict validation checks for mobile number and addresses."""
        headers = self._generate_hmac_headers()
        
        # Test 1: Invalid mobile number (less than 10 digits)
        bad_student = self.student_data.copy()
        bad_student["mobile"] = "12345"
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(bad_student),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = self._decrypt(response.json())
        self.assertEqual(data.get("status"), "error")

        # Test 2: Invalid address pincode (too short)
        bad_student2 = self.student_data.copy()
        bad_student2["current_address"] = {"line": "St", "city": "D", "pincode": "123"}
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(bad_student2),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data2 = self._decrypt(response.json())
        self.assertEqual(data2.get("status"), "error")

    def test_receive_application_idempotency_and_pending_kyc(self):
        """Test POST /receive is strictly idempotent and sets initial kyc_status to PENDING."""
        headers = self._generate_hmac_headers()

        # First request
        response1 = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        data1 = self._decrypt(response1.json())
        tracking_id1 = data1["data"]["tracking_id"]

        # Verify student created with kyc_status PENDING
        student = Student.objects.get(tracking_id=tracking_id1)
        self.assertEqual(student.kyc_status, "PENDING")
        self.assertEqual(student.application_status, "PROCESSING")

        # Verify ABCApiLog stored encrypted_response_payload
        log = ABCApiLog.objects.filter(endpoint='/v1/issuer-bank/application/receive').first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.encrypted_response_payload)
        self.assertIn("encryptedData", log.encrypted_response_payload)

        # Second request (exact same student data)
        response2 = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        data2 = self._decrypt(response2.json())
        tracking_id2 = data2["data"]["tracking_id"]

        # Ensure both tracking IDs are identical (idempotent output)
        self.assertEqual(tracking_id1, tracking_id2)
        # Ensure only 1 student record was created
        self.assertEqual(Student.objects.filter(apaar_id=self.student_data["apaar_id"]).count(), 1)

    # 3. Pull Application Status API Tests (Encrypted & TWA Client interaction)
    def test_application_status_pull_encrypted(self):
        """Test GET /status/{tracking_id} returns encrypted response and logs encrypted_response_payload."""
        headers = self._generate_hmac_headers()
        # Create student record
        response1 = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(self.student_data),
            content_type='application/json',
            **headers
        )
        data1 = self._decrypt(response1.json())
        tracking_id = data1["data"]["tracking_id"]

        # Request status
        headers_status = self._generate_hmac_headers()
        response2 = self.client.generic(
            'GET',
            reverse('issuer_bank:application_status', kwargs={"tracking_id": tracking_id}),
            content_type='application/json',
            **headers_status
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertIn("encryptedData", response2.json())
        
        status_data = self._decrypt(response2.json())
        self.assertEqual(status_data["data"]["tracking_id"], tracking_id)
        self.assertEqual(status_data["data"]["kyc_status"], "PENDING")

        # Verify ABCApiLog
        log = ABCApiLog.objects.filter(direction='inbound').order_by('-created_at').first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.encrypted_response_payload)
        self.assertIn("encryptedData", log.encrypted_response_payload)
        self.assertEqual(log.response_payload["data"]["tracking_id"], tracking_id)

    # 4. Dashboard Stats API Tests (Encrypted response & PENDING bucket)
    def test_dashboard_stats_encrypted(self):
        """Test GET /stats returns encrypted response with PENDING status counts."""
        headers = self._generate_hmac_headers()
        # Insert a few students in database
        Student.objects.create(
            tracking_id="TAP-STATS-1",
            apaar_id="APAAR-S1",
            mobile="1234567890",
            email="s1@ex.com",
            application_status="RECEIVED",
            kyc_status="PENDING"
        )
        Student.objects.create(
            tracking_id="TAP-STATS-2",
            apaar_id="APAAR-S2",
            mobile="1234567890",
            email="s2@ex.com",
            application_status="PROCESSING",
            kyc_status="FULL_KYC"
        )

        headers_stats = self._generate_hmac_headers()
        response = self.client.get(
            reverse('issuer_bank:dashboard_stats'),
            **headers_stats
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("encryptedData", response.json())
        
        data = self._decrypt(response.json())
        self.assertEqual(data["data"]["total_students"], 2)
        self.assertEqual(data["data"]["application_status_counts"]["RECEIVED"], 1)
        self.assertEqual(data["data"]["application_status_counts"]["PROCESSING"], 1)
        self.assertEqual(data["data"]["kyc_status_counts"]["PENDING"], 1)
        self.assertEqual(data["data"]["kyc_status_counts"]["FULL_KYC"], 1)

        # Verify ABCApiLog
        log = ABCApiLog.objects.filter(endpoint='/v1/issuer-bank/dashboard/stats').first()
        self.assertIsNotNone(log)
        self.assertIsNotNone(log.encrypted_response_payload)
        self.assertIn("encryptedData", log.encrypted_response_payload)

    # 5. Nested ABC submit payload flattening
    def test_nested_abc_submit_flattening(self):
        """Test pushing nested ABC_submit.json structured data is correctly flattened and saved."""
        nested_payload = {
            "APAAR_ID": "APAAR-NESTED-9988",
            "PERSONAL_INFO": {
                "FULL_NAME": "Aditya Roy",
                "DOB": "1999-12-15",
                "GENDER": "M",
                "MOBILE": "9876543210",
                "EMAIL": "aditya@example.com"
            },
            "ACADEMIC_INFO": {
                "UNIVERSITY_NAME": "Mumbai University",
                "COLLEGE_NAME": "K.J. Somaiya",
                "COURSE_NAME": "B.Tech",
                "ENROLLMENT_NUMBER": "ENR001",
                "ADMISSION_YEAR": 2024,
                "ACADEMIC_SESSION": "2022-2025",
                "ACADEMIC_STATUS": "ACTIVE"
            },
            "ADDITIONAL_INFO": {
                "BLOOD_GROUP": "O+",
                "CURRENT_ADDRESS": {
                    "ADDRESS_LINE": "A-101 XYZ Colony",
                    "CITY": "Mumbai",
                    "STATE": "Maharashtra",
                    "PIN_CODE": "400001"
                },
                "PERMANENT_ADDRESS": {
                    "ADDRESS_LINE": "456 Civil Lines",
                    "CITY": "Patna",
                    "STATE": "Bihar",
                    "PIN_CODE": "800001",
                    "SAME_AS_CURRENT": False
                }
            },
            "PHOTO_INFO": {
                "PHOTO_NAME": "aditya_photo.jpg",
                "PHOTO_PATH": "/uploads/photos/aditya_photo.jpg",
                "PHOTO_FULL_PATH": "https://img.freepik.com/free-photo/handsome-young-businessman_144627-28565.jpg",
                "PHOTO_TYPE": "image/jpeg",
                "PHOTO_SIZE_KB": 250
            },
            "APPLICATION_META": {
                "STATUS": "SUBMITTED"
            },
            "APPLICATION_REFERENCE_NUMBER": "APAAR-NESTED-9988-REF"
        }

        headers = self._generate_hmac_headers()
        response = self.client.post(
            reverse('issuer_bank:receive_application'),
            data=self._encrypt(nested_payload),
            content_type='application/json',
            **headers
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = self._decrypt(response.json())
        self.assertEqual(data.get("status"), "success")

        # Fetch student and verify flattened database mapping
        student = Student.objects.get(apaar_id="APAAR-NESTED-9988")
        self.assertEqual(student.full_name, "Aditya Roy")
        self.assertEqual(student.title, "Mr")
        self.assertEqual(student.gender, "M")
        self.assertEqual(student.kyc_status, "PENDING")

    def test_health_check_endpoint(self):
        """Test that the GET /health/ health check endpoint returns 200 and indicates a healthy database."""
        response = self.client.get(reverse('health_check'))
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("database"), "connected")


