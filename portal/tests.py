import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from django.conf import settings
from rest_framework import status

from applications.models import Student, AadhaarApiLog
from cms.models import CMSPage
from m2p.client import M2PClient

class PortalTestCase(TestCase):
    def setUp(self):
        # Create a test student in the database
        self.student = Student.objects.create(
            tracking_id="TAP-PORTAL-TEST",
            apaar_id="APAAR-98765-43210",
            full_name="Vikesh Sharma Portal",
            dob="2000-01-01",
            gender="M",
            mobile="9876543210",
            email="portal-test@example.com",
            university_name="IIT Delhi",
            college_name="IITD Main",
            course_name="B.Tech Computer Science",
            enrollment_number="IITD-12345",
            admission_year=2020,
            academic_session="2020-2024",
            academic_status="Active",
            current_address={"city": "Delhi", "pincode": "110016"},
            permanent_address={"city": "Jaipur", "pincode": "302001"},
            application_status="RECEIVED",
            kyc_status="MIN_KYC"
        )

        settings.M2P_BASE_URL = "https://kycuat.yappay.in"
        settings.M2P_TENANT = "TRANSCORP"
        settings.CASHFREE_BASE_URL = "https://sandbox.cashfree.com/verification"
        settings.CASHFREE_CLIENT_ID = "CF_MOCK_ID"
        settings.CASHFREE_CLIENT_SECRET = "CF_MOCK_SECRET"

    def test_portal_landing_get_active(self):
        """Test GET /portal/<tracking_id>/ returns 200 OK for active student."""
        response = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/landing.html')
        self.assertContains(response, self.student.full_name)

    def test_portal_landing_get_locked(self):
        """Test GET /portal/<tracking_id>/ redirects to locked out screen if student is locked."""
        self.student.otp_locked = True
        self.student.save()

        response = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id}))

        # GET locked view renders 200
        response_locked = self.client.get(
            reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response_locked.status_code, 200)
        self.assertTemplateUsed(response_locked, 'portal/locked.html')

    @patch('applications.aadhaar_client.AadhaarClient.aadhaar_send_otp')
    @patch('m2p.client.M2PClient.generate_otp')
    def test_portal_landing_send_otp_success(self, mock_generate_otp, mock_send_otp):
        """Test POST /aadhaar/send-otp/ saves Aadhaar and ref_id on success."""
        mock_send_otp.return_value = {"status": "SUCCESS", "ref_id": "REF-MOCK-A1"}
        mock_generate_otp.return_value = {"success": True}

        response = self.client.post(
            reverse('portal:aadhaar_send_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"aadhaar_number": "123456789012", "consent": True}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ref_id"], "REF-MOCK-A1")

        # Verify student Aadhaar info is saved
        self.student.refresh_from_db()
        self.assertEqual(self.student.aadhaar_number, "123456789012")
        self.assertEqual(self.student.aadhaar_ref_id, "REF-MOCK-A1")

    def test_portal_landing_send_otp_validation(self):
        """Test POST /aadhaar/send-otp/ validation rules (Aadhaar length and consent checkbox)."""
        # Test 1: Missing consent
        response1 = self.client.post(
            reverse('portal:aadhaar_send_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"aadhaar_number": "123456789012", "consent": False}),
            content_type='application/json'
        )
        self.assertEqual(response1.status_code, 400)
        self.assertIn("consent", response1.json()["error"])

        # Test 2: Invalid Aadhaar length
        response2 = self.client.post(
            reverse('portal:aadhaar_send_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"aadhaar_number": "12345", "consent": True}),
            content_type='application/json'
        )
        self.assertEqual(response2.status_code, 400)
        self.assertIn("exactly 12 digits", response2.json()["error"])

    @patch('applications.aadhaar_client.AadhaarClient.aadhaar_verify_otp')
    @patch('applications.aadhaar_client.AadhaarClient.name_match')
    @patch('m2p.client.M2PClient.generate_otp')
    def test_portal_verify_otp_success(self, mock_generate, mock_name_match, mock_verify_otp):
        """Test POST /aadhaar/verify-otp/ success redirects to M2P OTP verify page."""
        self.student.aadhaar_number = "123456789012"
        self.student.aadhaar_ref_id = "REF-MOCK-A1"
        self.student.save()

        mock_verify_otp.return_value = {"status": "SUCCESS", "data": {"name": "Vikesh Sharma Portal"}}
        mock_name_match.return_value = {"status": "SUCCESS", "data": {"score": 0.95}} # 95%
        mock_generate.return_value = {"success": True}

        response = self.client.post(
            reverse('portal:aadhaar_verify_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"otp": "123456", "ref_id": "REF-MOCK-A1"}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("/otp/", data["redirect_url"])

        # Verify student record transitioned
        self.student.refresh_from_db()
        self.assertEqual(self.student.otp_attempt_count, 0)
        self.assertTrue(self.student.aadhaar_verified)
        self.assertEqual(self.student.aadhaar_name_match_score, 95)
        mock_generate.assert_called_once()

    @patch('applications.aadhaar_client.AadhaarClient.aadhaar_verify_otp')
    @patch('applications.aadhaar_client.AadhaarClient.name_match')
    def test_portal_verify_otp_name_mismatch(self, mock_name_match, mock_verify_otp):
        """Test POST /aadhaar/verify-otp/ returns error when name match score is < 90%."""
        self.student.aadhaar_number = "123456789012"
        self.student.aadhaar_ref_id = "REF-MOCK-A1"
        self.student.save()

        mock_verify_otp.return_value = {"status": "SUCCESS", "data": {"name": "Some Other Name"}}
        mock_name_match.return_value = {"status": "SUCCESS", "data": {"score": 0.45}} # 45%

        response = self.client.post(
            reverse('portal:aadhaar_verify_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"otp": "123456", "ref_id": "REF-MOCK-A1"}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("Aadhaar name mismatch", data["error"])

        self.student.refresh_from_db()
        self.assertFalse(self.student.aadhaar_verified)
        self.assertEqual(self.student.aadhaar_name_match_score, 45)

    @patch('applications.aadhaar_client.AadhaarClient.aadhaar_verify_otp')
    def test_portal_verify_otp_attempts_lockout(self, mock_verify_otp):
        """Test POST /aadhaar/verify-otp/ fails limit locks student on 3rd attempt."""
        self.student.aadhaar_number = "123456789012"
        self.student.aadhaar_ref_id = "REF-MOCK-A1"
        self.student.save()

        mock_verify_otp.return_value = {"status": "FAILED", "message": "Incorrect OTP"}

        # Attempt 1
        response1 = self.client.post(
            reverse('portal:aadhaar_verify_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"otp": "111111", "ref_id": "REF-MOCK-A1"}),
            content_type='application/json'
        )
        self.assertEqual(response1.status_code, 200)
        self.assertFalse(response1.json()["success"])
        self.assertFalse(response1.json()["locked"])

        # Attempt 2
        response2 = self.client.post(
            reverse('portal:aadhaar_verify_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"otp": "222222", "ref_id": "REF-MOCK-A1"}),
            content_type='application/json'
        )
        self.assertEqual(response2.status_code, 200)
        self.assertFalse(response2.json()["success"])
        self.assertFalse(response2.json()["locked"])

        # Attempt 3 (locks student)
        response3 = self.client.post(
            reverse('portal:aadhaar_verify_otp', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps({"otp": "333333", "ref_id": "REF-MOCK-A1"}),
            content_type='application/json'
        )
        self.assertEqual(response3.status_code, 403)
        self.assertTrue(response3.json()["locked"])

        self.student.refresh_from_db()
        self.assertTrue(self.student.otp_locked)

    def test_success_get_without_min_kyc(self):
        """Test GET /success redirects to landing if kyc_status is not MIN_KYC."""
        self.student.kyc_status = "FAILED"
        self.student.save()

        response = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}))

    def test_success_get_with_min_kyc(self):
        """Test GET /success renders CMS content successfully."""
        self.student.kyc_status = "MIN_KYC"
        self.student.save()

        CMSPage.objects.create(
            slug="success-page",
            title="S1 Success",
            content="Download instruction mock text"
        )

        response = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S1 Success")

    @patch('requests.post')
    def test_aadhaar_client_no_masking(self, mock_post):
        """Test AadhaarClient logging creates an UNMASKED AadhaarApiLog entry."""
        from applications.aadhaar_client import AadhaarClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "SUCCESS",
            "ref_id": "REAL-AADHAAR-REF-12345"
        }
        mock_post.return_value = mock_resp

        client = AadhaarClient()
        response = client.aadhaar_send_otp("123456789012", self.student)
        self.assertEqual(response.get("ref_id"), "REAL-AADHAAR-REF-12345")

        log = AadhaarApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.student, self.student)
        self.assertEqual(log.endpoint, "aadhaar_send_otp")
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.success)

        # Assert Aadhaar number is NOT masked in the logged payload (User request)
        self.assertEqual(log.request_payload.get("aadhaar_number"), "123456789012")

    def test_m2p_otp_page_get_secure(self):
        """Test GET /portal/<tracking_id>/otp/ redirects to landing if Aadhaar not verified."""
        self.student.aadhaar_verified = False
        self.student.save()

        response = self.client.get(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}))

    def test_m2p_otp_page_get_success(self):
        """Test GET /portal/<tracking_id>/otp/ renders form when Aadhaar is verified."""
        self.student.aadhaar_verified = True
        self.student.save()

        response = self.client.get(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/otp.html')
        self.assertContains(response, "Remaining verification attempts")

    @patch('m2p.client.M2PClient.register_min_kyc')
    @patch('twa.client.TWAClient.sync_onboard')
    def test_m2p_otp_submission_success(self, mock_twa_sync, mock_m2p_register):
        """Test POST /portal/<tracking_id>/otp/ registers student successfully."""
        self.student.aadhaar_verified = True
        self.student.aadhaar_number = "123456789012"
        self.student.save()

        mock_m2p_register.return_value = {
            "success": True,
            "entityId": "APAAR-E2E-99999",
            "kitNo": "KIT-TEST-999",
            "token": "TOKEN-TEST-999"
        }

        # Mock TWA sync success
        def mock_sync(student):
            from twa.models import TWAApiLog
            TWAApiLog.objects.create(
                student=student,
                apaar_id=student.apaar_id,
                tracking_id=student.tracking_id,
                endpoint="sync_onboard",
                request_url="http://mock-twa",
                success=True,
                duration_ms=10
            )
        mock_twa_sync.side_effect = mock_sync

        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "123456"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

        self.student.refresh_from_db()
        self.assertEqual(self.student.kyc_status, "MIN_KYC")
        self.assertEqual(self.student.m2p_kit_no, "KIT-TEST-999")
        self.assertEqual(self.student.m2p_token, "TOKEN-TEST-999")
        self.assertTrue(self.student.twa_synced)

    @patch('m2p.client.M2PClient.register_min_kyc')
    def test_m2p_otp_submission_fail_limit(self, mock_m2p_register):
        """Test POST /portal/<tracking_id>/otp/ locks student on 3 failed attempts."""
        self.student.aadhaar_verified = True
        self.student.save()

        mock_m2p_register.return_value = {"success": False, "error": "Invalid OTP"}

        # Attempt 1
        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "111111"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remaining verification attempts")
        self.assertContains(response, "2 of 3")

        # Attempt 2
        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "222222"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remaining verification attempts")
        self.assertContains(response, "1 of 3")

        # Attempt 3 (redirects to locked)
        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "333333"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id}))

        self.student.refresh_from_db()
        self.assertTrue(self.student.otp_locked)
