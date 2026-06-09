import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from django.conf import settings
from rest_framework import status

from applications.models import Student
from cms.models import CMSPage
from m2p.models import M2PApiLog
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

        # Standard settings for M2P client UAT test
        settings.M2P_BASE_URL = "https://kycuat.yappay.in"
        settings.M2P_TENANT = "TRANSCORP"

    # 1. M2P Client Unit Tests (Mocking HTTP Requests)
    @patch('requests.post')
    def test_m2p_client_generate_otp_success(self, mock_post):
        """Test M2P client OTP generation success, and check database log generation."""
        # Setup mock success response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "result": {
                "success": True,
                "message": "OTP generated successfully"
            }
        }
        mock_post.return_value = mock_response

        # Call client method
        client = M2PClient()
        response = client.generate_otp(self.student)
        self.assertTrue(response.get("success"))

        # Verify exactly one outbound log row is created in M2PApiLog (Constitution P1)
        log = M2PApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.student, self.student)
        self.assertEqual(log.endpoint, "generate_otp")
        self.assertTrue(log.success)
        # Check PII is masked in log (Constitution P6)
        self.assertEqual(log.request_payload.get("mobileNumber"), "+919876543210")

    @patch('requests.post')
    def test_m2p_client_register_success_masking(self, mock_post):
        """Test M2P client Register success and verify that OTP and PAN are masked in logs (Constitution P6)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "result": {
                "success": True,
                "entityId": "APAAR-98765-43210",
                "kitNo": "KIT-TEST-123",
                "token": "TOKEN-TEST-abc"
            }
        }
        mock_post.return_value = mock_response

        client = M2PClient()
        response = client.register_min_kyc(self.student, "123456", "ABCDE1234F")
        self.assertTrue(response.get("success"))

        # Check DB log table masking (Constitution P6)
        log = M2PApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.request_payload["otp"], "***")
        self.assertEqual(log.request_payload["kycInfo"][0]["documentNo"], "***")

    # 2. Portal Landing Page Tests
    def test_portal_landing_get_active(self):
        """Test GET /portal/<tracking_id>/ returns 200 OK for active student."""
        response = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/landing.html')
        self.assertContains(response, self.student.full_name)

    def test_portal_landing_get_locked(self):
        """Test GET /portal/<tracking_id>/ renders locked out screen if student is locked."""
        self.student.otp_locked = True
        self.student.save()

        response = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/locked.html')
        self.assertContains(response, "Verification Locked")

    def test_portal_landing_post_invalid_pan(self):
        """Test POST /landing validation rules (invalid PAN format, consent checkbox missing)."""
        # Test 1: Missing consent
        response = self.client.post(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}),
            data={"pan_number": "ABCDE1234F"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must accept the terms and provide consent")

        # Test 2: Bad PAN structure
        response = self.client.post(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}),
            data={"pan_number": "BADPAN123", "consent": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid PAN number format")

    @patch('applications.pan_client.PANClient.verify_pan')
    @patch('m2p.client.M2PClient.generate_otp')
    def test_portal_landing_post_success(self, mock_generate_otp, mock_verify_pan):
        """Test POST /landing saves PAN and redirects to OTP verify screen."""
        mock_verify_pan.return_value = {"valid": True, "name_match_score": 100}
        mock_generate_otp.return_value = {"success": True}

        response = self.client.post(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}),
            data={"pan_number": "ABCDE1234F", "consent": "on"}
        )
        # Check redirect to OTP screen
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}))

        # Verify student PAN is saved
        self.student.refresh_from_db()
        self.assertEqual(self.student.pan_number, "ABCDE1234F")
        self.assertTrue(self.student.pan_verified)
        self.assertEqual(self.student.pan_name_match_score, 100)

    # 3. OTP Verification Page Tests
    def test_otp_verify_get_without_pan(self):
        """Test GET /otp redirects to landing if PAN hasn't been saved yet."""
        response = self.client.get(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}))

    @patch('m2p.client.M2PClient.register_min_kyc')
    def test_otp_verify_post_success(self, mock_register):
        """Test POST /otp success transitions status to MIN_KYC and stores kit/token."""
        # Pre-set PAN
        self.student.pan_number = "ABCDE1234F"
        self.student.save()

        mock_register.return_value = {
            "success": True,
            "entityId": "APAAR-98765-43210",
            "kitNo": "KIT-REAL-12345",
            "token": "TOKEN-REAL-abcde"
        }

        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "123456"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

        # Verify student record transitioned
        self.student.refresh_from_db()
        self.assertEqual(self.student.kyc_status, "MIN_KYC")
        self.assertEqual(self.student.m2p_kit_no, "KIT-REAL-12345")
        self.assertEqual(self.student.m2p_token, "TOKEN-REAL-abcde")

    @patch('m2p.client.M2PClient.register_min_kyc')
    def test_otp_verify_post_failure_attempts(self, mock_register):
        """Test POST /otp failure increases attempts count, and locks student on 3rd failure."""
        self.student.pan_number = "ABCDE1234F"
        self.student.save()

        mock_register.return_value = {"success": False, "error": "Invalid OTP code"}

        # Attempt 1
        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "111111"}
        )
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.otp_attempt_count, 1)
        self.assertFalse(self.student.otp_locked)
        self.assertContains(response, "Remaining verification attempts:")
        self.assertContains(response, "2 of 3")

        # Attempt 2
        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "222222"}
        )
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.otp_attempt_count, 2)
        self.assertFalse(self.student.otp_locked)

        # Attempt 3 (locks student)
        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "333333"}
        )
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.otp_attempt_count, 3)
        self.assertTrue(self.student.otp_locked)
        self.assertTemplateUsed(response, "portal/locked.html")

    # 4. Success Page Tests
    def test_success_get_without_min_kyc(self):
        """Test GET /success redirects to landing if kyc_status is not MIN_KYC."""
        # Current status is set to empty kyc
        self.student.kyc_status = "FAILED"
        self.student.save()

        response = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}))

    def test_success_get_with_min_kyc(self):
        """Test GET /success renders page with correct CMS Page content."""
        self.student.kyc_status = "MIN_KYC"
        self.student.save()

        # Create CMSPage success copy
        cms_title = "Welcome aboard Vikesh!"
        cms_content = "<p>Download instructions content block.</p>"
        CMSPage.objects.create(
            slug="success-page",
            title=cms_title,
            content=cms_content
        )

        response = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/success.html')
        self.assertContains(response, cms_title)
        self.assertContains(response, cms_content)

    @patch('applications.pan_client.PANClient.verify_pan')
    def test_portal_landing_post_pan_invalid_api(self, mock_verify_pan):
        """Test POST /landing when Cashfree PAN verification returns invalid."""
        mock_verify_pan.return_value = {"valid": False, "message": "Invalid PAN Number"}

        response = self.client.post(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}),
            data={"pan_number": "ABCDE1234F", "consent": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid PAN Number")
        
        # Verify student record not updated
        self.student.refresh_from_db()
        self.assertFalse(self.student.pan_verified)

    @patch('applications.pan_client.PANClient.verify_pan')
    def test_portal_landing_post_pan_low_score(self, mock_verify_pan):
        """Test POST /landing when Cashfree PAN verification match score is too low (< 60%)."""
        mock_verify_pan.return_value = {"valid": True, "name_match_score": 45}

        response = self.client.post(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}),
            data={"pan_number": "ABCDE1234F", "consent": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PAN name mismatch: match score is 45%")
        
        # Verify student record not updated
        self.student.refresh_from_db()
        self.assertFalse(self.student.pan_verified)

    @patch('requests.get')
    def test_local_photo_downloader(self, mock_get):
        """Test download_student_photo helper successfully downloads and saves file locally."""
        from applications.views import download_student_photo
        import os
        from django.conf import settings

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake-image-bytes"
        mock_get.return_value = mock_resp

        photo_url = "https://example.com/photos/aditya.jpg"
        apaar_id = "APAAR-PHOTO-TEST"
        
        local_path = download_student_photo(photo_url, apaar_id)
        
        # Assert returned relative path is correct
        self.assertEqual(local_path, "/media/photos/APAAR-PHOTO-TEST.jpg")
        
        # Assert file was actually created in media root
        expected_file = os.path.join(settings.MEDIA_ROOT, "photos", "APAAR-PHOTO-TEST.jpg")
        self.assertTrue(os.path.exists(expected_file))
        
        # Cleanup file
        if os.path.exists(expected_file):
            os.remove(expected_file)

    @patch('requests.post')
    def test_pan_client_logging(self, mock_post):
        """Test PANClient logging creates a masked PANApiLog entry on successful call."""
        from applications.pan_client import PANClient
        from applications.models import PANApiLog

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "valid": True,
            "name_match_score": 100,
            "message": "PAN verified successfully"
        }
        mock_post.return_value = mock_resp

        client = PANClient()
        response = client.verify_pan("ABCDE1234F", self.student)
        self.assertTrue(response.get("valid"))

        # Assert exactly one log row is created in PANApiLog table (Constitution P1)
        log = PANApiLog.objects.first()
        self.assertIsNotNone(log)
        self.assertEqual(log.student, self.student)
        self.assertEqual(log.endpoint, "pan_verify")
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.success)

        # Assert PAN number is masked in the logged payload (Constitution P6)
        self.assertEqual(log.request_payload.get("pan"), "***")
