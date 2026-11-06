import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
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
            kyc_status="PENDING"
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
        self.assertContains(response, 'href="https://transcorpint.com/wp-content/uploads/2026/06/transcorp-ppi-mitc.pdf"')

    def test_portal_landing_unblocks_locked_student(self):
        """Test GET /portal/<tracking_id>/ redirects to locked during cooldown and unblocks once cooldown expires."""
        from django.utils import timezone
        from datetime import timedelta
        self.student.otp_locked = True
        self.student.otp_locked_until = timezone.now() + timedelta(minutes=15)
        self.student.otp_attempt_count = 3
        self.student.aadhaar_number = "123456789012"
        self.student.aadhaar_ref_id = "REF-OLD-123"
        self.student.aadhaar_verified = True
        self.student.kyc_status = "PENDING"
        self.student.save()

        # Direct hit on locked view renders 200 with restart link and cooldown remaining
        response_locked = self.client.get(
            reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response_locked.status_code, 200)
        self.assertTemplateUsed(response_locked, 'portal/locked.html')
        self.assertContains(response_locked, reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id}))
        self.assertContains(response_locked, "cooldown period of")

        # While cooldown is active, landing page redirects to locked view
        response_active = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response_active.status_code, 302)
        self.assertRedirects(response_active, reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id}))

        # Now simulate expiration of cooldown
        self.student.otp_locked_until = timezone.now() - timedelta(minutes=1)
        self.student.save()

        # Hitting the main portal landing URL now unblocks the user
        response = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/landing.html')

        # Verify student was unblocked and fields reset
        self.student.refresh_from_db()
        self.assertFalse(self.student.otp_locked)
        self.assertIsNone(self.student.otp_locked_until)
        self.assertEqual(self.student.otp_attempt_count, 0)
        self.assertIsNone(self.student.aadhaar_number)
        self.assertIsNone(self.student.aadhaar_ref_id)
        self.assertFalse(self.student.aadhaar_verified)

        # Verify the old Aadhaar number is not echoed back in the response HTML
        self.assertNotContains(response, '123456789012')

    ALL_CONSENTS = {
        "consent_aadhaar_ovd": True,
        "consent_ckycr": True,
        "consent_address_mismatch": True,
        "consent_kyc_ppi": True,
        "consent_terms_conditions": True
    }

    def _start_url(self):
        return reverse('portal:digilocker_start', kwargs={'tracking_id': self.student.tracking_id})

    def _callback_url(self):
        return reverse('portal:digilocker_callback', kwargs={'tracking_id': self.student.tracking_id})

    def _landing_url(self):
        return reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_create_url')
    def test_digilocker_start_success(self, mock_create_url):
        """Test POST /aadhaar/digilocker/start/ returns the consent URL and saves verification_id + consents."""
        mock_create_url.return_value = {"status": "PENDING", "url": "https://digilocker.example/consent/abc"}

        response = self.client.post(
            self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["url"], "https://digilocker.example/consent/abc")

        self.student.refresh_from_db()
        self.assertTrue(self.student.aadhaar_ref_id.startswith("TAP-"))
        self.assertTrue(self.student.consent_aadhaar_ovd)
        self.assertTrue(self.student.consent_terms_conditions)

        # Cashfree gets the verification_id and the callback URL on this portal
        verification_id, callback_url, _student = mock_create_url.call_args[0]
        self.assertEqual(verification_id, self.student.aadhaar_ref_id)
        self.assertTrue(callback_url.endswith("/aadhaar/digilocker/callback/"))

    @override_settings(MOCK_CASHFREE_APIS=False, DIGILOCKER_REDIRECT_BASE_URL='', PORTAL_BASE_URL='')
    @patch('applications.aadhaar_client.AadhaarClient.digilocker_create_url')
    def test_digilocker_start_rejects_non_https_redirect(self, mock_create_url):
        """Test start fails early (no Cashfree call) when the callback URL would not be https, e.g. localhost."""
        response = self.client.post(
            self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json'
        )
        self.assertEqual(response.status_code, 500)
        self.assertIn("https", response.json()["error"])
        mock_create_url.assert_not_called()

    @override_settings(MOCK_CASHFREE_APIS=False, DIGILOCKER_REDIRECT_BASE_URL='https://tap.example.com/')
    @patch('applications.aadhaar_client.AadhaarClient.digilocker_create_url')
    def test_digilocker_start_uses_redirect_base_url(self, mock_create_url):
        """Test DIGILOCKER_REDIRECT_BASE_URL overrides the request host for the callback URL."""
        mock_create_url.return_value = {"status": "PENDING", "url": "https://digilocker.example/consent/abc"}
        response = self.client.post(
            self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        callback_url = mock_create_url.call_args[0][1]
        self.assertEqual(
            callback_url,
            f"https://tap.example.com/portal/{self.student.tracking_id}/aadhaar/digilocker/callback/"
        )

    def test_digilocker_start_requires_all_consents(self):
        """Test POST /aadhaar/digilocker/start/ rejects missing consents."""
        response = self.client.post(
            self._start_url(),
            data=json.dumps({"consent_aadhaar_ovd": False}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("consents", response.json()["error"])

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_create_url')
    def test_digilocker_start_cashfree_failure(self, mock_create_url):
        """Test POST /aadhaar/digilocker/start/ surfaces a Cashfree error without saving a verification_id."""
        mock_create_url.return_value = {"message": "Insufficient balance"}

        response = self.client.post(
            self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error"], "Insufficient balance")
        self.student.refresh_from_db()
        self.assertIsNone(self.student.aadhaar_ref_id)

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_create_url')
    def test_digilocker_start_rate_limited(self, mock_create_url):
        """Test a second start within the cooldown returns HTTP 429."""
        mock_create_url.return_value = {"status": "PENDING", "url": "https://digilocker.example/consent/abc"}
        first = self.client.post(self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json')
        self.assertEqual(first.status_code, 200)
        second = self.client.post(self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json')
        self.assertEqual(second.status_code, 429)
        self.assertEqual(mock_create_url.call_count, 1)

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_document')
    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    @patch('applications.aadhaar_client.AadhaarClient.name_match')
    @patch('m2p.client.M2PClient.generate_otp')
    def test_digilocker_callback_success(self, mock_generate, mock_name_match, mock_status, mock_document):
        """Test the callback verifies status, fetches the document, name-matches and redirects to the M2P OTP page."""
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.otp_attempt_count = 1
        self.student.save()

        mock_status.return_value = {"status": "AUTHENTICATED"}
        mock_document.return_value = {"name": "Vikesh Sharma Portal", "aadhaar_number": "XXXXXXXX9012"}
        mock_name_match.return_value = {"status": "SUCCESS", "data": {"score": 0.95}}
        mock_generate.return_value = {"success": True}

        response = self.client.get(self._callback_url())
        self.assertRedirects(
            response, reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            fetch_redirect_response=False
        )

        self.student.refresh_from_db()
        self.assertTrue(self.student.aadhaar_verified)
        self.assertEqual(self.student.aadhaar_number, "9012")
        self.assertEqual(self.student.aadhaar_name_match_score, 95)
        self.assertEqual(self.student.otp_attempt_count, 0)
        mock_status.assert_called_once()
        mock_generate.assert_called_once()

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_document')
    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    @patch('m2p.client.M2PClient.generate_otp')
    def test_digilocker_callback_is_idempotent(self, mock_generate, mock_status, mock_document):
        """Test a repeated callback after verification does not call Cashfree or M2P again."""
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.aadhaar_verified = True
        self.student.save()

        response = self.client.get(self._callback_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/otp/", response.url)
        mock_status.assert_not_called()
        mock_document.assert_not_called()
        mock_generate.assert_not_called()

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    def test_digilocker_callback_pending(self, mock_status):
        """Test PENDING status returns to landing with a message and keeps the verification_id."""
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.save()
        mock_status.return_value = {"status": "PENDING"}

        response = self.client.get(self._callback_url())
        self.assertRedirects(response, self._landing_url() + "?dl=pending", fetch_redirect_response=False)
        self.student.refresh_from_db()
        self.assertEqual(self.student.aadhaar_ref_id, "TAP-VID-1")
        self.assertFalse(self.student.aadhaar_verified)

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    def test_digilocker_callback_expired_and_denied(self, mock_status):
        """Test EXPIRED / CONSENT_DENIED clear the verification_id so the student can restart."""
        for dl_status, key in (("EXPIRED", "expired"), ("CONSENT_DENIED", "denied")):
            self.student.aadhaar_ref_id = "TAP-VID-1"
            self.student.save()
            mock_status.return_value = {"status": dl_status}

            response = self.client.get(self._callback_url())
            self.assertRedirects(response, self._landing_url() + f"?dl={key}", fetch_redirect_response=False)
            self.student.refresh_from_db()
            self.assertIsNone(self.student.aadhaar_ref_id)

        landing = self.client.get(self._landing_url() + "?dl=denied")
        self.assertContains(landing, "declined consent")

    def test_digilocker_callback_without_start(self):
        """Test a callback with no verification in progress returns to landing with an error message."""
        response = self.client.get(self._callback_url())
        self.assertRedirects(response, self._landing_url() + "?dl=error", fetch_redirect_response=False)

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_document')
    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    @patch('applications.aadhaar_client.AadhaarClient.name_match')
    def test_digilocker_callback_name_mismatch(self, mock_name_match, mock_status, mock_document):
        """Test a name match below 90% is rejected, counts as an attempt and clears the verification_id."""
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.save()
        mock_status.return_value = {"status": "AUTHENTICATED"}
        mock_document.return_value = {"name": "Some Other Name", "aadhaar_number": "XXXXXXXX9012"}
        mock_name_match.return_value = {"status": "SUCCESS", "data": {"score": 0.45}}

        response = self.client.get(self._callback_url())
        self.assertRedirects(response, self._landing_url() + "?dl=mismatch", fetch_redirect_response=False)

        self.student.refresh_from_db()
        self.assertFalse(self.student.aadhaar_verified)
        self.assertEqual(self.student.aadhaar_name_match_score, 45)
        self.assertEqual(self.student.otp_attempt_count, 1)
        self.assertIsNone(self.student.aadhaar_ref_id)

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_document')
    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    @patch('applications.aadhaar_client.AadhaarClient.name_match')
    def test_digilocker_callback_name_mismatch_locks_on_third_attempt(self, mock_name_match, mock_status, mock_document):
        """Test the third failed name match locks the student."""
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.otp_attempt_count = 2
        self.student.save()
        mock_status.return_value = {"status": "AUTHENTICATED"}
        mock_document.return_value = {"name": "Some Other Name", "aadhaar_number": "XXXXXXXX9012"}
        mock_name_match.return_value = {"status": "SUCCESS", "data": {"score": 0.45}}

        response = self.client.get(self._callback_url())
        self.assertRedirects(
            response, reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id}),
            fetch_redirect_response=False
        )
        self.student.refresh_from_db()
        self.assertTrue(self.student.otp_locked)

    @patch('applications.aadhaar_client.AadhaarClient.digilocker_get_status')
    def test_digilocker_callback_cashfree_error(self, mock_status):
        """Test a Cashfree exception returns to landing with an error message instead of a 500."""
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.save()
        mock_status.side_effect = Exception("network down")

        response = self.client.get(self._callback_url())
        self.assertRedirects(response, self._landing_url() + "?dl=error", fetch_redirect_response=False)

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
        """Test GET /success renders CMS content and App download QR code."""
        self.student.kyc_status = "MIN_KYC"
        self.student.save()

        response = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration Successful!")
        self.assertContains(response, "app_download_qr.png")
        self.assertContains(response, "Scan to Download")

    def test_portal_landing_redirects_completed_student(self):
        """Test GET /portal/<tracking_id>/ redirects to success if student already completed MIN KYC."""
        self.student.kyc_status = "MIN_KYC"
        self.student.save()

        response = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

    @override_settings(MOCK_CASHFREE_APIS=False, DIGILOCKER_USER_FLOW='signup')
    @patch('requests.post')
    def test_digilocker_create_url_request_and_log(self, mock_post):
        """Test create-URL sends the Cashfree DigiLocker payload and writes one log row."""
        from applications.aadhaar_client import AadhaarClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"verification_id": "TAP-VID-1", "status": "PENDING", "url": "https://digilocker.example/x"}
        mock_post.return_value = mock_resp

        response = AadhaarClient().digilocker_create_url("TAP-VID-1", "https://portal.example/cb/", self.student)
        self.assertEqual(response["url"], "https://digilocker.example/x")

        self.assertEqual(mock_post.call_args[0][0], "https://sandbox.cashfree.com/verification/digilocker")
        sent = mock_post.call_args[1]['json']
        self.assertEqual(sent["verification_id"], "TAP-VID-1")
        self.assertEqual(sent["document_requested"], ["AADHAAR"])
        self.assertEqual(sent["redirect_url"], "https://portal.example/cb/")
        self.assertEqual(sent["user_flow"], "signup")

        log = AadhaarApiLog.objects.get()
        self.assertEqual(log.student, self.student)
        self.assertEqual(log.endpoint, "digilocker_create_url")
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.success)
        self.assertEqual(log.request_headers["X-Client-Secret"], "******")

    @override_settings(MOCK_CASHFREE_APIS=False)
    @patch('requests.get')
    def test_digilocker_get_document_logs_masked_aadhaar_only(self, mock_get):
        """Test get-document queries by verification_id and logs never hold more than the last 4 Aadhaar digits."""
        from applications.aadhaar_client import AadhaarClient

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"name": "Vikesh Sharma Portal", "aadhaar_number": "XXXXXXXX9012"}
        mock_get.return_value = mock_resp

        response = AadhaarClient().digilocker_get_document("TAP-VID-1", self.student)
        self.assertEqual(response["name"], "Vikesh Sharma Portal")
        self.assertEqual(mock_get.call_args[0][0], "https://sandbox.cashfree.com/verification/digilocker/document/AADHAAR")
        self.assertEqual(mock_get.call_args[1]['params'], {"verification_id": "TAP-VID-1"})

        log = AadhaarApiLog.objects.get()
        self.assertEqual(log.endpoint, "digilocker_document")
        self.assertTrue(log.success)
        self.assertEqual(log.response_payload["aadhaar_number"], "9012")

    @override_settings(MOCK_CASHFREE_APIS=False)
    @patch('requests.get')
    def test_digilocker_get_status_failure_is_logged(self, mock_get):
        """Test a non-200 Cashfree response is logged as a failure with its message."""
        from applications.aadhaar_client import AadhaarClient

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"message": "Invalid clientId and clientSecret combination"}
        mock_get.return_value = mock_resp

        AadhaarClient().digilocker_get_status("TAP-VID-1", self.student)
        log = AadhaarApiLog.objects.get()
        self.assertEqual(log.endpoint, "digilocker_status")
        self.assertFalse(log.success)
        self.assertEqual(log.error_message, "Invalid clientId and clientSecret combination")

    @override_settings(MOCK_CASHFREE_APIS=True)
    def test_digilocker_mock_mode_end_to_end(self):
        """Test mock mode walks start -> callback with no external calls and lands on the M2P OTP page."""
        with patch('m2p.client.M2PClient.generate_otp', return_value={"success": True}):
            start = self.client.post(self._start_url(), data=json.dumps(self.ALL_CONSENTS), content_type='application/json')
            self.assertTrue(start.json()["success"])
            self.assertIn("/aadhaar/digilocker/callback/", start.json()["url"])

            callback = self.client.get(self._callback_url())
            self.assertRedirects(
                callback, reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
                fetch_redirect_response=False
            )

        self.student.refresh_from_db()
        self.assertTrue(self.student.aadhaar_verified)
        self.assertEqual(self.student.aadhaar_number, "9012")
        self.assertEqual(
            sorted(AadhaarApiLog.objects.values_list('endpoint', flat=True)),
            ["digilocker_create_url", "digilocker_document", "digilocker_status", "name_match"]
        )

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
        self.student.aadhaar_number = "9012"
        self.student.aadhaar_ref_id = "TAP-VID-1"
        self.student.save()

        mock_m2p_register.return_value = {
            "success": True,
            "result": {
                "entityId": "APAAR-E2E-99999",
                "cardDetails": {
                    "kitNumber": "KIT-TEST-999"
                },
                "token": "TOKEN-TEST-999"
            }
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
        # Assert the DigiLocker verification reference was passed to M2P as the document ref
        self.assertEqual(mock_m2p_register.call_args[0][2], "TAP-VID-1")
        self.assertEqual(self.student.kyc_status, "MIN_KYC")
        self.assertEqual(self.student.m2p_kit_no, "KIT-TEST-999")
        self.assertEqual(self.student.m2p_token, "TOKEN-TEST-999")
        # Assert only last 4 digits are stored in the database
        self.assertEqual(self.student.aadhaar_number, "9012")
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

    def test_format_m2p_error_helper(self):
        """Test format_m2p_error cleanly extracts human-readable error messages."""
        from m2p.client import format_m2p_error

        # 1. User's exact Y409 corporate issuance duplicate error
        y409_payload = {
            "result": None,
            "exception": {
                "cause": None,
                "message": None,
                "errorCode": "Y409",
                "suppressed": [],
                "fieldErrors": None,
                "languageCode": "en",
                "shortMessage": "Corporate Issuance already exist for application number: APP-377973430902",
                "detailMessage": "Corporate Issuance already exist for application number: APP-377973430902",
                "localizedMessage": None
            },
            "pagination": None
        }
        extracted = format_m2p_error(y409_payload)
        self.assertEqual(
            extracted, 
            "Corporate Issuance already exist for application number: APP-377973430902 (Code: Y409)"
        )

        # 2. String exception
        self.assertEqual(format_m2p_error({"exception": "Invalid OTP code"}), "Invalid OTP code")

        # 3. Field errors list
        fe_payload = {
            "exception": {
                "errorCode": "Y400",
                "fieldErrors": [{"message": "Mobile number is invalid"}]
            }
        }
        self.assertEqual(format_m2p_error(fe_payload), "Mobile number is invalid (Code: Y400)")

    @patch('m2p.client.M2PClient.register_min_kyc')
    def test_m2p_otp_submission_with_y409_error_display(self, mock_m2p_register):
        """Test POST /portal/<tracking_id>/otp/ formats M2P Y409 error properly on page."""
        self.student.aadhaar_verified = True
        self.student.save()

        mock_m2p_register.return_value = {
            "result": None,
            "exception": {
                "cause": None,
                "message": None,
                "errorCode": "Y409",
                "suppressed": [],
                "fieldErrors": None,
                "languageCode": "en",
                "shortMessage": "Corporate Issuance already exist for application number: APP-377973430902",
                "detailMessage": "Corporate Issuance already exist for application number: APP-377973430902",
                "localizedMessage": None
            },
            "pagination": None
        }

        response = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "123456"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corporate Issuance already exist for application number: APP-377973430902")
        self.assertContains(response, "Code: Y409")
        # Ensure raw dict representation is not shown
        self.assertNotContains(response, "{'cause': None")

    @patch('m2p.client.M2PClient.generate_otp')
    def test_m2p_resend_otp_success(self, mock_generate_otp):
        """Test POST /portal/<tracking_id>/m2p/resend-otp/ succeeds when Aadhaar is verified."""
        self.student.aadhaar_verified = True
        self.student.save()

        mock_generate_otp.return_value = {"success": True, "result": {"success": True}}

        response = self.client.post(
            reverse('portal:m2p_resend_otp', kwargs={'tracking_id': self.student.tracking_id}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("A new OTP has been sent", data["message"])

    def test_m2p_resend_otp_aadhaar_not_verified(self):
        """Test POST /portal/<tracking_id>/m2p/resend-otp/ returns 400 if Aadhaar is not verified."""
        self.student.aadhaar_verified = False
        self.student.save()

        response = self.client.post(
            reverse('portal:m2p_resend_otp', kwargs={'tracking_id': self.student.tracking_id}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_m2p_resend_otp_locked_user(self):
        """Test POST /portal/<tracking_id>/m2p/resend-otp/ returns 403 if user is locked."""
        self.student.aadhaar_verified = True
        self.student.otp_locked = True
        self.student.save()

        response = self.client.post(
            reverse('portal:m2p_resend_otp', kwargs={'tracking_id': self.student.tracking_id}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["locked"])

    def test_portal_views_never_cache_headers(self):
        """Test that portal views send Cache-Control headers preventing stale bfcache loading."""
        # 1. Landing view
        res_landing = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        cache_ctrl = res_landing.headers.get('Cache-Control', '')
        self.assertIn('no-cache', cache_ctrl)
        self.assertIn('no-store', cache_ctrl)

        # 2. Success view (for verified student)
        self.student.kyc_status = 'MIN_KYC'
        self.student.save()
        res_success = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        cache_ctrl_success = res_success.headers.get('Cache-Control', '')
        self.assertIn('no-cache', cache_ctrl_success)
        self.assertIn('no-store', cache_ctrl_success)

        # 3. Locked view (for locked student)
        self.student.otp_locked = True
        self.student.save()
        res_locked = self.client.get(
            reverse('portal:locked', kwargs={'tracking_id': self.student.tracking_id})
        )
        cache_ctrl_locked = res_locked.headers.get('Cache-Control', '')
        self.assertIn('no-cache', cache_ctrl_locked)
        self.assertIn('no-store', cache_ctrl_locked)

    def test_completed_kyc_back_navigation_redirects(self):
        """Test that already completed KYC students attempting back navigation to forms get redirected to success."""
        self.student.kyc_status = 'MIN_KYC'
        self.student.aadhaar_verified = True
        self.student.save()

        # 1. Back to landing page -> redirects to success
        res_landing = self.client.get(
            reverse('portal:landing', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertRedirects(res_landing, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

        # 2. Back to OTP verify GET -> redirects to success
        res_otp_get = self.client.get(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertRedirects(res_otp_get, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

        # 3. Re-post OTP verify POST -> redirects to success
        res_otp_post = self.client.post(
            reverse('portal:otp_verify', kwargs={'tracking_id': self.student.tracking_id}),
            data={"otp": "123456"}
        )
        self.assertRedirects(res_otp_post, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

        # 4. DigiLocker start POST -> rejected with 400 and redirect_url
        res_dl_start = self.client.post(
            reverse('portal:digilocker_start', kwargs={'tracking_id': self.student.tracking_id}),
            data=json.dumps(self.ALL_CONSENTS),
            content_type='application/json'
        )
        self.assertEqual(res_dl_start.status_code, 400)
        self.assertIn(reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}), res_dl_start.json().get('redirect_url', ''))

        # 5. DigiLocker callback GET -> redirects to success
        res_dl_callback = self.client.get(
            reverse('portal:digilocker_callback', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertRedirects(res_dl_callback, reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id}))

    @patch('m2p.client.M2PClient.generate_otp')
    def test_rate_limiting_m2p_resend_otp(self, mock_generate_otp):
        """Test that calling M2P Resend OTP within 30 seconds triggers HTTP 429 rate limit."""
        self.student.aadhaar_verified = True
        self.student.save()

        mock_generate_otp.return_value = {"success": True, "result": {"success": True}}

        # First request succeeds
        res1 = self.client.post(
            reverse('portal:m2p_resend_otp', kwargs={'tracking_id': self.student.tracking_id}),
            content_type='application/json'
        )
        self.assertEqual(res1.status_code, 200)

        # Immediate second request fails with 429
        res2 = self.client.post(
            reverse('portal:m2p_resend_otp', kwargs={'tracking_id': self.student.tracking_id}),
            content_type='application/json'
        )
        self.assertEqual(res2.status_code, 429)
        self.assertIn("Please wait", res2.json()["error"])

    def test_cms_custom_content_persistence(self):
        """Test that admin customizations to CMSPage content are NOT overwritten by SuccessView."""
        self.student.kyc_status = 'MIN_KYC'
        self.student.save()

        custom_text = "<p>Welcome to our custom VIP onboarding for {mobile}!</p>"
        CMSPage.objects.update_or_create(
            slug='success-page',
            defaults={
                'title': 'Custom Success Title',
                'content': custom_text
            }
        )

        response = self.client.get(
            reverse('portal:success', kwargs={'tracking_id': self.student.tracking_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom Success Title")
        self.assertContains(response, "custom VIP onboarding")

        # Verify DB content was NOT overwritten back to default_content
        page = CMSPage.objects.get(slug='success-page')
        self.assertEqual(page.content, custom_text)
