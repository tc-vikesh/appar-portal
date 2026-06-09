import json
import time
from unittest.mock import patch, MagicMock
import requests
from django.test import TestCase
from django.urls import reverse
from django.conf import settings
from rest_framework import status

from applications.models import Student
from webhooks.models import WebhookLog
from webhooks.dispatcher import ABCWebhookDispatcher


class WebhookDispatcherTestCase(TestCase):
    def setUp(self):
        # Create a test student
        self.student = Student.objects.create(
            tracking_id="TAP-ABC-OUT-TEST",
            apaar_id="APAAR-ABC-11111",
            full_name="Vikesh Sharma ABC Out",
            dob="1998-05-15",
            gender="M",
            mobile="9876543210",
            email="abc-out@example.com",
            university_name="IIT Delhi",
            college_name="IITD Main",
            course_name="M.Tech CS",
            enrollment_number="IITD-999",
            admission_year=2022,
            academic_session="2022-2024",
            academic_status="Active",
            current_address={"city": "Delhi", "pincode": "110016"},
            permanent_address={"city": "Jaipur", "pincode": "302001"},
            pan_number="ABCDE1234F",
            m2p_entity_id="APAAR-ABC-11111",
            m2p_kit_no="KIT-ABC-1234",
            m2p_token="TOKEN-ABC-5678",
            application_status="RECEIVED",
            kyc_status="MIN_KYC",
            twa_synced=True
        )

        # Standard settings for outbound webhook
        settings.ABC_CLIENT_ID = "mock_abc_client_id_tap"
        settings.ABC_CLIENT_SECRET = "mock_abc_client_secret_hmac_sha256"
        settings.ABC_STATUS_UPDATE_WEBHOOK_URL = "http://localhost:8001/webhook/abc/application/status/update"
        settings.ABC_KYC_STATUS_WEBHOOK_URL = "http://localhost:8001/webhook/abc/application/kyc-status"
        settings.TWA_WEBHOOK_ALLOWED_IPS = ["127.0.0.1"]

    # 1. Dispatcher Unit Tests (Success cases)
    @patch('requests.post')
    def test_dispatch_application_status_update_success(self, mock_post):
        """Test successful application status update webhook send and WebhookLog creation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "message": "Received"}
        mock_post.return_value = mock_response

        # Call dispatcher
        dispatcher = ABCWebhookDispatcher()
        res = dispatcher.dispatch_application_status_update(self.student, "Remarks text")

        self.assertTrue(res.get("success"))

        # Verify requests.post call details
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], settings.ABC_STATUS_UPDATE_WEBHOOK_URL)
        
        # Verify no auth header is sent (no-auth UAT specs)
        self.assertNotIn("Authorization", kwargs.get("headers", {}))
        
        payload = kwargs.get("json", {})
        self.assertEqual(payload.get("tracking_id"), self.student.tracking_id)
        self.assertEqual(payload.get("application_status"), self.student.application_status)
        self.assertEqual(payload.get("remarks"), "Remarks text")

        # Verify exactly one WebhookLog outbound row is written (Constitution P1)
        log = WebhookLog.objects.filter(direction='outbound').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.student, self.student)
        self.assertEqual(log.tracking_id, self.student.tracking_id)
        self.assertEqual(log.webhook_type, 'outbound_abc_app_status')
        self.assertEqual(log.endpoint, settings.ABC_STATUS_UPDATE_WEBHOOK_URL)
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.success)

    @patch('requests.post')
    def test_dispatch_kyc_status_update_success(self, mock_post):
        """Test successful KYC status update webhook send."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        # Call dispatcher
        dispatcher = ABCWebhookDispatcher()
        res = dispatcher.dispatch_kyc_status_update(self.student, "KYC verified")

        self.assertTrue(res.get("success"))

        # Check DB log row
        log = WebhookLog.objects.filter(direction='outbound').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.webhook_type, 'outbound_abc_kyc_status')
        self.assertEqual(log.endpoint, settings.ABC_KYC_STATUS_WEBHOOK_URL)
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.success)

    # 2. Dispatcher Unit Tests (Failure cases - logged silently)
    @patch('requests.post')
    def test_dispatch_http_failure_logged_silently(self, mock_post):
        """Test dispatcher handles non-2xx response from ABC silently, creating failed WebhookLog."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.side_effect = ValueError("No JSON")
        mock_post.return_value = mock_response

        # Call dispatcher and verify it does NOT crash (silent non-blocking webhook)
        dispatcher = ABCWebhookDispatcher()
        res = dispatcher.dispatch_application_status_update(self.student, "Should not raise")

        self.assertFalse(res.get("success"))
        
        # Verify exactly one failed WebhookLog outbound row is written
        log = WebhookLog.objects.filter(direction='outbound').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.http_status, 500)
        self.assertFalse(log.success)
        self.assertIn("Non-2xx HTTP status: 500", log.error_message)

    @patch('requests.post')
    def test_dispatch_exception_logged_silently(self, mock_post):
        """Test dispatcher catches requests connection errors silently and creates failed log row."""
        mock_post.side_effect = requests.exceptions.ConnectTimeout("Connection timed out")

        # Call dispatcher and verify it is silent
        dispatcher = ABCWebhookDispatcher()
        res = dispatcher.dispatch_kyc_status_update(self.student, "Timeout test")

        self.assertFalse(res.get("success"))

        # Verify exactly one failed WebhookLog outbound row is written (Constitution P1)
        log = WebhookLog.objects.filter(direction='outbound').first()
        self.assertIsNotNone(log)
        self.assertIsNone(log.http_status)
        self.assertFalse(log.success)
        self.assertIn("ConnectTimeout", log.error_message)

    def test_dispatch_not_configured_logged(self):
        """Test dispatcher skips send if setting is empty but still logs failure."""
        settings.ABC_STATUS_UPDATE_WEBHOOK_URL = ""

        dispatcher = ABCWebhookDispatcher()
        res = dispatcher.dispatch_application_status_update(self.student, "Skip send")

        self.assertFalse(res.get("success"))

        log = WebhookLog.objects.filter(direction='outbound').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.endpoint, 'NOT_CONFIGURED')
        self.assertFalse(log.success)
        self.assertIn("Webhook URL is not configured", log.error_message)

    # 3. Integration Tests (Inbound webhook triggers outbound webhook)
    @patch('requests.post')
    def test_inbound_twa_app_status_webhook_triggers_outbound_abc_webhook(self, mock_post):
        """Verify that receiving an inbound application status update from TWA triggers outbound dispatch to ABC."""
        # Setup mock success for outbound call to ABC
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        # Execute inbound webhook call from TWA
        url = reverse('twa:application_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "processing_status": "ISSUED",
            "remarks": "Card has been dispatched"
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1' # Whitelisted IP
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify student status transitioned in DB
        self.student.refresh_from_db()
        self.assertEqual(self.student.application_status, "ISSUED")

        # Verify outbound ABC dispatcher call was triggered
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], settings.ABC_STATUS_UPDATE_WEBHOOK_URL)

        # Verify exactly one inbound log and exactly one outbound log were created
        inbound_log = WebhookLog.objects.filter(direction='inbound').first()
        outbound_log = WebhookLog.objects.filter(direction='outbound').first()

        self.assertIsNotNone(inbound_log)
        self.assertIsNotNone(outbound_log)

        self.assertEqual(inbound_log.webhook_type, 'inbound_twa_app_status')
        self.assertTrue(inbound_log.success)

        self.assertEqual(outbound_log.webhook_type, 'outbound_abc_app_status')
        self.assertTrue(outbound_log.success)
        self.assertEqual(outbound_log.http_status, 200)

    @patch('requests.post')
    def test_inbound_twa_kyc_status_webhook_triggers_outbound_abc_webhook(self, mock_post):
        """Verify that receiving an inbound KYC status update from TWA triggers outbound dispatch to ABC."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        # Inbound webhook call
        url = reverse('twa:kyc_status_webhook')
        payload = {
            "tracking_id": self.student.tracking_id,
            "kyc_status": "FULL_KYC",
            "remarks": "Video KYC success"
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify student transitioned
        self.student.refresh_from_db()
        self.assertEqual(self.student.kyc_status, "FULL_KYC")

        # Verify E2E outbound trigger
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], settings.ABC_KYC_STATUS_WEBHOOK_URL)

        inbound_log = WebhookLog.objects.filter(direction='inbound').first()
        outbound_log = WebhookLog.objects.filter(direction='outbound').first()

        self.assertIsNotNone(inbound_log)
        self.assertIsNotNone(outbound_log)
        self.assertTrue(inbound_log.success)
        self.assertTrue(outbound_log.success)

    @patch('requests.post')
    def test_acknowledge_endpoint_triggers_outbound_webhook(self, mock_post):
        """Verify that POST /v1/issuer-bank/application/acknowledge triggers outbound dispatch to ABC."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        # Execute inbound ABC acknowledge call (bypass signature check via mocking/HMAC headers or using helper)
        # Note: Since applications views are protected by HMACAuthentication, let's generate valid HMAC headers
        # or we can simply test direct view or mock authentication.
        # Let's bypass HMAC auth by patching applications HMAC authentication or by constructing a valid signature.
        # Constructing a valid signature:
        # message = client_secret + client_id + timestamp
        # signature = SHA256(message).hexdigest()
        import hashlib
        client_id = "mock_abc_client_id_tap"
        client_secret = "mock_abc_client_secret_hmac_sha256"
        timestamp = str(int(time.time()))
        message = client_secret + client_id + timestamp
        signature = hashlib.sha256(message.encode('utf-8')).hexdigest()

        # Call acknowledge
        url = reverse('issuer_bank:acknowledge_application')
        payload = {"tracking_id": self.student.tracking_id}

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_CLIENT_ID=client_id,
            HTTP_X_CLIENT_TIMESTAMP=timestamp,
            HTTP_X_CLIENT_HMAC=signature
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify DB transitioned
        self.student.refresh_from_db()
        self.assertEqual(self.student.application_status, "PROCESSING")

        # Verify outbound ABC dispatcher call was triggered
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], settings.ABC_STATUS_UPDATE_WEBHOOK_URL)
