import re
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.urls import reverse

from applications.models import Student
from cms.models import CMSPage
from m2p.client import M2PClient
from twa.client import TWAClient

class LandingView(View):
    """
    GET /portal/<tracking_id>/
    Renders landing screen for student, displaying details and collecting PAN/consent.
    """
    def get(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Check if student is locked out due to too many OTP attempts
        if student.otp_locked:
            return render(request, 'portal/locked.html', {'student': student})

        return render(request, 'portal/landing.html', {'student': student})

    def post(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        if student.otp_locked:
            return render(request, 'portal/locked.html', {'student': student})

        pan_number = request.POST.get('pan_number', '').strip().upper()
        consent = request.POST.get('consent') == 'on'

        errors = {}
        if not consent:
            errors['consent'] = "You must accept the terms and provide consent to proceed."

        # PAN regex: 5 letters, 4 digits, 1 letter
        if not pan_number:
            errors['pan_number'] = "PAN number is required."
        elif not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$', pan_number):
            errors['pan_number'] = "Invalid PAN number format (should be like ABCDE1234F)."

        if errors:
            return render(request, 'portal/landing.html', {
                'student': student,
                'errors': errors,
                'pan_number': pan_number,
                'consent': consent
            })

        # Call Cashfree PAN Verification client
        from applications.pan_client import PANClient
        pan_client = PANClient()
        try:
            pan_resp = pan_client.verify_pan(pan_number, student)
            is_valid = pan_resp.get("valid") is True
            match_score = float(pan_resp.get("name_match_score", 0))

            if not is_valid:
                errors['pan_number'] = pan_resp.get("message") or "Invalid PAN number."
            elif match_score < 60.0:
                errors['pan_number'] = f"PAN name mismatch: match score is {int(match_score)}% (minimum 60% required)."

            if not errors:
                # Save verified PAN and score on student record
                student.pan_number = pan_number
                student.pan_verified = True
                student.pan_name_match_score = int(match_score)
                student.save()
        except Exception as exc:
            errors['pan_number'] = f"PAN verification service error: {str(exc)}"

        if errors:
            return render(request, 'portal/landing.html', {
                'student': student,
                'errors': errors,
                'pan_number': pan_number,
                'consent': consent
            })

        # Call M2P Client to generate OTP
        m2p = M2PClient()
        try:
            m2p_response = m2p.generate_otp(student)
            
            # Check M2P generate otp success status
            m2p_success = (
                m2p_response.get("success") is True or 
                m2p_response.get("result", {}).get("success") is True
            )

            if m2p_success:
                # Redirect to OTP verify screen
                return redirect(reverse('portal:otp_verify', kwargs={'tracking_id': tracking_id}))
            else:
                err_msg = m2p_response.get("error") or m2p_response.get("exception") or "Failed to generate OTP from KYC provider."
                errors['m2p'] = err_msg

        except Exception as exc:
            errors['m2p'] = f"KYC server error: {str(exc)}"

        return render(request, 'portal/landing.html', {
            'student': student,
            'errors': errors,
            'pan_number': pan_number,
            'consent': consent
        })


class OTPVerifyView(View):
    """
    GET/POST /portal/<tracking_id>/otp/
    Student enters OTP to register for MIN KYC. Enforces a maximum of 3 attempts.
    """
    def get(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Redirect if PAN is not yet entered
        if not student.pan_number:
            return redirect(reverse('portal:landing', kwargs={'tracking_id': tracking_id}))

        if student.otp_locked:
            return render(request, 'portal/locked.html', {'student': student})

        remaining_attempts = max(3 - student.otp_attempt_count, 0)
        return render(request, 'portal/otp.html', {
            'student': student,
            'remaining_attempts': remaining_attempts
        })

    def post(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        if not student.pan_number:
            return redirect(reverse('portal:landing', kwargs={'tracking_id': tracking_id}))

        if student.otp_locked:
            return render(request, 'portal/locked.html', {'student': student})

        otp = request.POST.get('otp', '').strip()
        errors = {}

        if not otp:
            errors['otp'] = "OTP is required."
            remaining_attempts = max(3 - student.otp_attempt_count, 0)
            return render(request, 'portal/otp.html', {
                'student': student,
                'errors': errors,
                'remaining_attempts': remaining_attempts
            })

        # Increment attempt count
        student.otp_attempt_count += 1
        student.save()

        # Check attempt limit
        if student.otp_attempt_count > 3:
            student.otp_locked = True
            student.save()
            return render(request, 'portal/locked.html', {'student': student})

        remaining_attempts = max(3 - student.otp_attempt_count, 0)

        # Call M2P Client to Register MIN KYC
        m2p = M2PClient()
        try:
            m2p_response = m2p.register_min_kyc(student, otp, student.pan_number)
            m2p_success = (
                m2p_response.get("success") is True or 
                m2p_response.get("result", {}).get("success") is True or
                (m2p_response.get("result") is not None and (
                    "kitNo" in m2p_response["result"] or 
                    "token" in m2p_response["result"] or 
                    "entityId" in m2p_response["result"]
                ))
            )

            if m2p_success:
                # Store details in student record
                # UAT mocks or actual responses give entityId, kitNo, token
                result_data = m2p_response.get("result", {}) if "result" in m2p_response else m2p_response
                student.m2p_entity_id = result_data.get("entityId") or student.apaar_id
                student.m2p_kit_no = result_data.get("kitNo") or "KIT-MOCK-12345"
                student.m2p_token = result_data.get("token") or "TOKEN-MOCK-abcde"

                # Transition statuses
                student.kyc_status = 'MIN_KYC'
                student.save()

                # Call TWA Sync Client (Stage) - Non-blocking
                twa = TWAClient()
                try:
                    twa.sync_onboard(student)
                    latest_log = student.twa_logs.filter(endpoint='sync_onboard').order_by('-created_at').first()
                    if latest_log and latest_log.success:
                        student.twa_synced = True
                        student.save()
                except Exception:
                    pass

                return redirect(reverse('portal:success', kwargs={'tracking_id': tracking_id}))
            else:
                err_msg = m2p_response.get("error") or m2p_response.get("exception") or "Invalid OTP code entered."
                errors['m2p'] = err_msg

        except Exception as exc:
            errors['m2p'] = f"KYC server error: {str(exc)}"

        # If they hit 3 attempts exactly and failed, lock them out immediately
        if student.otp_attempt_count >= 3:
            student.otp_locked = True
            student.save()
            return render(request, 'portal/locked.html', {'student': student})

        return render(request, 'portal/otp.html', {
            'student': student,
            'errors': errors,
            'remaining_attempts': remaining_attempts
        })


class SuccessView(View):
    """
    GET /portal/<tracking_id>/success/
    Displays app download instructions using CMSPage content.
    """
    def get(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Secure: Ensure student has actually completed MIN KYC first
        if student.kyc_status != 'MIN_KYC':
            return redirect(reverse('portal:landing', kwargs={'tracking_id': tracking_id}))

        # Load success page CMS content
        cms_page, created = CMSPage.objects.get_or_create(
            slug='success-page',
            defaults={
                'title': 'Registration Successful!',
                'content': '<p>Congratulations! Your MIN KYC has been verified successfully.</p><h4>Next Steps:</h4><ol><li>Download the <strong>Transcorp Web App (TWA)</strong>.</li><li>Log in using your mobile number <strong>+91 ' + student.mobile + '</strong>.</li><li>Complete full video KYC and track your digital card delivery directly inside the app!</li></ol>'
            }
        )

        return render(request, 'portal/success.html', {
            'student': student,
            'cms_page': cms_page
        })
