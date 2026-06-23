import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from django.conf import settings
from rest_framework import status

from applications.models import Student
from twa.models import TWAApiLog
from twa.client import TWAClient
from webhooks.models import WebhookLog


class TWATestCase(TestCase):
    def setUp(self):
        # Create a test student in the database
        self.student = Student.objects.create(
            tracking_id="TAP-TWA-TEST",
            apaar_id="APAAR-12345-67890",
            full_name="Vikesh Sharma TWA",
            dob="1999-12-31",
            gender="M",
            mobile="9876543210",
            email="twa-test@example.com",
            university_name="IIT Delhi",
            college_name="IITD Main",
            course_name="B.Tech CS",
            enrollment_number="IITD-123",
            admission_year=2021,
            academic_session="2021-2025",
            academic_status="Active",
            current_address={"city": "Delhi", "pincode": "110016"},
            permanent_address={"city": "Jaipur", "pincode": "302001"},
            aadhaar_number="123456789012",
            m2p_entity_id="APAAR-12345-67890",
            m2p_kit_no="KIT-TEST-1234",
            m2p_token="TOKEN-TEST-5678",
            application_status="RECEIVED",
            kyc_status="MIN_KYC",
            twa_synced=False
        )

        # Standard settings for TWA UAT test
        settings.TWA_SYNC_URL = "https://api.stage.transcorpint.com/user/external/onboard"
        settings.TWA_AUTH_TOKEN = "TEST_AUTH_TOKEN"
        settings.TWA_WEBHOOK_ALLOWED_IPS = ["127.0.0.1", "10.0.0.5"]

    # 1. TWA Client Unit Tests (Outbound)
    @patch('requests.post')
    def test_twa_client_sync_onboard_success(self, mock_post):
        """Test TWAClient sync_onboard outbound call success and TWAApiLog creation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "message": "Onboarding data synchronized"
        }
        mock_post.return_value = mock_response

        client = TWAClient()
        response = client.sync_onboard(self.student)

        self.assertTrue(response.get("success"))

        # Verify exactly one outbound log row is created in TWAApiLog (Constitution P1)
        log = TWAApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.student, self.student)
        self.assertEqual(log.endpoint, "sync_onboard")
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.success)

        # Verify that request payload is logged and has core fields
        self.assertEqual(log.request_payload.get("entityId"), self.student.apaar_id)
        # Verify sensitive Aadhaar and Token fields are logged in plain text (no masking)
        self.assertEqual(log.request_payload.get("idValue"), "123456789012")
        self.assertEqual(log.request_payload.get("vcipToken"), "TOKEN-TEST-5678")

    @patch('requests.post')
    def test_twa_client_sync_onboard_http_failure(self, mock_post):
        """Test TWAClient sync_onboard handles non-2xx status and logs the failure."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.side_effect = ValueError("No JSON")
        mock_post.return_value = mock_response

        client = TWAClient()
        response = client.sync_onboard(self.student)

        # Even with non-2xx response, it returns the body fallback and logs the failure
        self.assertIn("_raw_body", response)
        log = TWAApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.http_status, 500)
        self.assertFalse(log.success)
        self.assertEqual(log.error_message, "Non-success TWA response")

    @patch('requests.post')
    def test_twa_client_sync_onboard_exception(self, mock_post):
        """Test TWAClient sync_onboard handles exception and still logs inside finally block (Constitution P1)."""
        mock_post.side_effect = Exception("Connection Timeout")

        client = TWAClient()
        with self.assertRaises(Exception):
            client.sync_onboard(self.student)

        # Verify exactly one outbound log row is created despite the exception
        log = TWAApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.student, self.student)
        self.assertFalse(log.success)
        self.assertIn("Connection Timeout", log.error_message)

    # 2. Inbound Webhooks: IP Whitelisting & WebhookLog (Constitution P1)
    def test_inbound_webhook_blocked_by_ip_whitelisting(self):
        """Test that requests from non-whitelisted IP are blocked with 403 but still logged (Constitution P1)."""
        url = reverse('twa:application_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "processing_status": "PROCESSING",
            "remarks": "Initiated card dispatch"
        }

        # Issue request with a non-whitelisted IP
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='192.168.1.99'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json(), {"error": "Forbidden: IP not whitelisted."})

        # Verify that exactly one WebhookLog is written (Constitution P1)
        log = WebhookLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.direction, 'inbound')
        self.assertEqual(log.webhook_type, 'inbound_twa_app_status')
        self.assertEqual(log.source_ip, '192.168.1.99')
        self.assertFalse(log.ip_whitelisted)
        self.assertFalse(log.success)
        self.assertEqual(log.http_status, 403)
        self.assertEqual(log.tracking_id, self.student.tracking_id)
        self.assertEqual(log.student, self.student)

    def test_inbound_webhook_allowed_by_ip_whitelisting(self):
        """Test that requests from whitelisted IP are allowed and process correctly."""
        url = reverse('twa:application_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "processing_status": "PROCESSING",
            "remarks": "In progress"
        }

        # Issue request with a whitelisted IP
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify student database status is transitioned
        self.student.refresh_from_db()
        self.assertEqual(self.student.application_status, "PROCESSING")

        # Verify that exactly one WebhookLog is written
        log = WebhookLog.objects.filter(direction='inbound').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.direction, 'inbound')
        self.assertEqual(log.webhook_type, 'inbound_twa_app_status')
        self.assertEqual(log.source_ip, '127.0.0.1')
        self.assertTrue(log.ip_whitelisted)
        self.assertTrue(log.success)
        self.assertEqual(log.http_status, 200)

    # 3. Inbound Webhooks: Validation Rules
    def test_inbound_application_status_webhook_invalid_status(self):
        """Test webhook rejection on invalid application status."""
        url = reverse('twa:application_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "processing_status": "INVALID_STATE",
            "remarks": "Test invalid status"
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Verify log entry exists and is unsuccessful
        log = WebhookLog.objects.first()
        self.assertIsNotNone(log)
        self.assertFalse(log.success)
        self.assertEqual(log.http_status, 400)

    def test_inbound_application_status_webhook_missing_fields(self):
        """Test webhook rejection on missing mandatory fields."""
        url = reverse('twa:application_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id
            # missing processing_status
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        log = WebhookLog.objects.first()
        self.assertIsNotNone(log)
        self.assertFalse(log.success)

    # 4. KYC Status Webhook Tests
    def test_inbound_kyc_status_webhook_success(self):
        """Test KYC status webhook success and student kyc_status transition."""
        url = reverse('twa:kyc_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "kyc_status": "FULL_KYC",
            "remarks": "Video KYC completed"
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='10.0.0.5' # Whitelisted IP
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.student.refresh_from_db()
        self.assertEqual(self.student.kyc_status, "FULL_KYC")

        log = WebhookLog.objects.filter(direction='inbound').first()
        self.assertIsNotNone(log)
        self.assertTrue(log.ip_whitelisted)
        self.assertTrue(log.success)
        self.assertEqual(log.http_status, 200)

    def test_inbound_kyc_status_webhook_invalid_status(self):
        """Test KYC status webhook blocks invalid kyc_status value."""
        url = reverse('twa:kyc_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "kyc_status": "NOT_A_REAL_KYC_STATUS",
            "remarks": "Invalid"
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        log = WebhookLog.objects.first()
        self.assertIsNotNone(log)
        self.assertFalse(log.success)

    # 5. Masking PII in inbound webhook payloads (Constitution P6)
    def test_inbound_webhook_pii_masking(self):
        """Verify that sensitive fields in webhook payloads are masked before DB storage (Constitution P6)."""
        url = reverse('twa:application_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "processing_status": "PROCESSING",
            "pan_number": "ABCDE1234F",
            "otp": "999888",
            "auth_token": "secret_token_123",
            "full_name": "Vikesh Sharma TWA"
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Get the logged WebhookLog row
        log = WebhookLog.objects.filter(direction='inbound').first()
        self.assertIsNotNone(log)
        
        # Verify sensitive fields are NOT masked in DB payload column (no masking)
        logged_payload = log.payload
        self.assertEqual(logged_payload.get("pan_number"), "ABCDE1234F")
        self.assertEqual(logged_payload.get("otp"), "999888")
        self.assertEqual(logged_payload.get("auth_token"), "secret_token_123")
        self.assertEqual(logged_payload.get("full_name"), "Vikesh Sharma TWA")
        
        # Non-sensitive fields should be unchanged
        self.assertEqual(logged_payload.get("tracking_id"), self.student.tracking_id)
        self.assertEqual(logged_payload.get("processing_status"), "PROCESSING")

    # 6. Integration Flow: Portal OTP Verify to TWA Sync
    @patch('applications.aadhaar_client.AadhaarClient.aadhaar_verify_otp')
    @patch('applications.aadhaar_client.AadhaarClient.name_match')
    @patch('m2p.client.M2PClient.generate_otp')
    @patch('m2p.client.M2PClient.register_min_kyc')
    @patch('twa.client.TWAClient.sync_onboard')
    def test_portal_otp_success_triggers_twa_sync(self, mock_twa_sync, mock_m2p_register, mock_m2p_generate, mock_name_match, mock_verify_otp):
        """Verify that when a student completes KYC in portal, it triggers outbound TWA onboard sync."""
        self.student.aadhaar_number = "123456789012"
        self.student.aadhaar_ref_id = "REF-MOCK-A1"
        self.student.otp_attempt_count = 0
        self.student.save()

        mock_verify_otp.return_value = {"status": "SUCCESS", "data": {"name": "Vikesh Sharma TWA"}}
        mock_name_match.return_value = {"status": "SUCCESS", "data": {"score": 0.95}}
        mock_m2p_generate.return_value = {"success": True}

        mock_m2p_register.return_value = {
            "success": True,
            "result": {
                "success": True,
                "entityId": "APAAR-12345-67890",
                "kitNo": "KIT-TEST-123",
                "token": "TOKEN-TEST-abc"
            }
        }

        mock_twa_sync.return_value = {
            "success": True,
            "message": "User sync succeeded"
        }

        def mock_sync_side_effect(student):
            TWAApiLog.objects.create(
                student=student,
                apaar_id=student.apaar_id,
                tracking_id=student.tracking_id,
                endpoint='sync_onboard',
                request_url='https://api.stage.transcorpint.com/user/external/onboard',
                request_payload={},
                http_status=200,
                success=True,
                duration_ms=50
            )
            return {"success": True}
        mock_twa_sync.side_effect = mock_sync_side_effect

        # Step 1: Verify Aadhaar OTP (redirects to M2P OTP page)
        verify_url = reverse('portal:aadhaar_verify_otp', kwargs={'tracking_id': self.student.tracking_id})
        response = self.client.post(
            verify_url,
            data=json.dumps({'otp': '123456', 'ref_id': 'REF-MOCK-A1'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertIn("/otp/", response.json()["redirect_url"])

        # Step 2: Submit M2P OTP (completes registration and triggers TWA sync)
        otp_url = reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id})
        response = self.client.post(otp_url, data={'otp': '999888'})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

        mock_twa_sync.assert_called_once()
        self.student.refresh_from_db()
        self.assertTrue(self.student.twa_synced)
        self.assertEqual(self.student.kyc_status, "MIN_KYC")
