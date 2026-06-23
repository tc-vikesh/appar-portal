import re
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.urls import reverse
from django.http import JsonResponse

from applications.models import Student
from cms.models import CMSPage
from m2p.client import M2PClient
from twa.client import TWAClient
from applications.aadhaar_client import AadhaarClient

class LandingView(View):
    """
    GET /portal/<tracking_id>/
    Renders landing screen for student, displaying details.
    """
    def get(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Check if student is locked out due to too many OTP attempts
        if student.otp_locked:
            return redirect(reverse('portal:locked', kwargs={'tracking_id': tracking_id}))

        return render(request, 'portal/landing.html', {'student': student})


class AadhaarSendOTPView(View):
    """
    POST /portal/<tracking_id>/aadhaar/send-otp/
    AJAX endpoint to initiate Aadhaar verification by sending an OTP.
    """
    def post(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        if student.otp_locked:
            return JsonResponse({"success": False, "error": "Verification locked due to too many failed attempts.", "locked": True}, status=403)

        try:
            body = json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({"success": False, "error": "Invalid JSON payload."}, status=400)

        aadhaar_number = body.get('aadhaar_number', '').strip()
        consent = body.get('consent') is True

        if not consent:
            return JsonResponse({"success": False, "error": "You must accept the terms and provide consent to proceed."}, status=400)

        if not aadhaar_number:
            return JsonResponse({"success": False, "error": "Aadhaar number is required."}, status=400)
        elif not re.match(r'^\d{12}$', aadhaar_number):
            return JsonResponse({"success": False, "error": "Invalid Aadhaar number format. It must be exactly 12 digits."}, status=400)

        client = AadhaarClient()
        try:
            resp = client.aadhaar_send_otp(aadhaar_number, student)
            
            success = resp.get("status") == "SUCCESS"
            
            if success:
                ref_id = resp.get("ref_id") or resp.get("data", {}).get("ref_id")
                # Save aadhaar number and ref_id on student record
                student.aadhaar_number = aadhaar_number
                student.aadhaar_ref_id = ref_id
                student.save()

                return JsonResponse({"success": True, "ref_id": ref_id})
            else:
                err_msg = resp.get("message") or "Failed to trigger Aadhaar OTP via Cashfree."
                return JsonResponse({"success": False, "error": err_msg})
        except Exception as exc:
            return JsonResponse({"success": False, "error": f"Aadhaar service error: {str(exc)}"}, status=500)


class AadhaarVerifyOTPView(View):
    """
    POST /portal/<tracking_id>/aadhaar/verify-otp/
    AJAX endpoint to verify OTP, name-match and proceed to M2P registration.
    """
    def post(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        if student.otp_locked:
            return JsonResponse({"success": False, "error": "Verification locked.", "locked": True}, status=403)

        try:
            body = json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({"success": False, "error": "Invalid JSON payload."}, status=400)

        otp = body.get('otp', '').strip()
        ref_id = body.get('ref_id', '').strip()

        if not otp:
            return JsonResponse({"success": False, "error": "OTP is required."}, status=400)
        if not ref_id:
            return JsonResponse({"success": False, "error": "Reference ID is missing."}, status=400)

        # Increment attempt count
        student.otp_attempt_count += 1
        student.save()

        # Check attempt limit
        if student.otp_attempt_count > 3:
            student.otp_locked = True
            student.save()
            return JsonResponse({"success": False, "error": "Too many failed attempts. Verification locked.", "locked": True}, status=403)

        client = AadhaarClient()
        try:
            # Verify OTP
            verify_resp = client.aadhaar_verify_otp(otp, ref_id, student)
            verify_success = verify_resp.get("status") in ("VALID", "SUCCESS")
            
            if not verify_success:
                err_msg = verify_resp.get("message") or "Invalid Aadhaar OTP code."
                # If they hit 3 attempts exactly and failed, lock them out
                if student.otp_attempt_count >= 3:
                    student.otp_locked = True
                    student.save()
                    return JsonResponse({"success": False, "error": err_msg, "locked": True}, status=403)
                
                remaining = max(3 - student.otp_attempt_count, 0)
                return JsonResponse({"success": False, "error": f"{err_msg} ({remaining} attempts remaining)", "locked": False})

            # Retrieve Aadhaar name
            data_block = verify_resp.get("data", {}) if "data" in verify_resp else verify_resp
            aadhaar_name = data_block.get("name") or data_block.get("aadhaar_name") or ""
            
            if not aadhaar_name:
                return JsonResponse({"success": False, "error": "Failed to retrieve name from Aadhaar verification response."})

            # Call Name Match
            name_resp = client.name_match(student.full_name, aadhaar_name, student)
            name_success = name_resp.get("status") == "SUCCESS" or "score" in name_resp or ("data" in name_resp and "score" in name_resp.get("data", {}))
            
            if not name_success:
                err_msg = name_resp.get("message") or "Name matching service failed."
                return JsonResponse({"success": False, "error": err_msg})

            score_block = name_resp.get("data", {}) if "data" in name_resp else name_resp
            score_val = score_block.get("score")
            if score_val is None:
                score_val = 0.0
            
            match_score = float(score_val)
            if match_score <= 1.0:
                match_score = match_score * 100.0

            # Store match score
            student.aadhaar_name_match_score = int(match_score)
            if match_score < 90.0:
                student.save()
                return JsonResponse({"success": False, "error": f"Aadhaar name mismatch: match score is {int(match_score)}% (minimum 90% required)."})

            student.aadhaar_verified = True
            student.save()

            # Proceed to register with M2P - first generate OTP and redirect to M2P verification page
            m2p = M2PClient()
            try:
                m2p_response = m2p.generate_otp(student)
                m2p_success = (
                    m2p_response.get("success") is True or 
                    m2p_response.get("result", {}).get("success") is True
                )

                if m2p_success:
                    # Reset attempts count for the M2P OTP verification stage
                    student.otp_attempt_count = 0
                    student.save()
                    return JsonResponse({"success": True, "redirect_url": reverse('portal:otp_verify', kwargs={'tracking_id': tracking_id})})
                else:
                    err_msg = m2p_response.get("error") or m2p_response.get("exception") or "Failed to generate OTP from KYC provider."
                    return JsonResponse({"success": False, "error": f"KYC OTP generation failed: {err_msg}"})

            except Exception as exc:
                return JsonResponse({"success": False, "error": f"KYC server OTP generation error: {str(exc)}"}, status=500)
        except Exception as exc:
            return JsonResponse({"success": False, "error": f"Verification server error: {str(exc)}"}, status=500)


class OTPVerifyView(View):
    """
    GET/POST /portal/<tracking_id>/otp/
    Student enters M2P OTP to register for MIN KYC. Enforces a maximum of 3 attempts.
    """
    def get(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        # Redirect if Aadhaar is not yet verified
        if not student.aadhaar_verified:
            return redirect(reverse('portal:landing', kwargs={'tracking_id': tracking_id}))

        if student.otp_locked:
            return redirect(reverse('portal:locked', kwargs={'tracking_id': tracking_id}))

        remaining_attempts = max(3 - student.otp_attempt_count, 0)
        return render(request, 'portal/otp.html', {
            'student': student,
            'remaining_attempts': remaining_attempts
        })

    def post(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)

        if not student.aadhaar_verified:
            return redirect(reverse('portal:landing', kwargs={'tracking_id': tracking_id}))

        if student.otp_locked:
            return redirect(reverse('portal:locked', kwargs={'tracking_id': tracking_id}))

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
            return redirect(reverse('portal:locked', kwargs={'tracking_id': tracking_id}))

        remaining_attempts = max(3 - student.otp_attempt_count, 0)

        # Call M2P Client to Register MIN KYC
        m2p = M2PClient()
        try:
            m2p_response = m2p.register_min_kyc(student, otp, student.aadhaar_number)
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
            return redirect(reverse('portal:locked', kwargs={'tracking_id': tracking_id}))
        return render(request, 'portal/otp.html', {
            'student': student,
            'errors': errors,
            'remaining_attempts': remaining_attempts
        })


class SuccessView(View):
    """
    GET /portal/<tracking_id>/success/
    Displays app download instructions.
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


class LockedView(View):
    """
    GET /portal/<tracking_id>/locked/
    Renders locked out screen if student has too many failed attempts.
    """
    def get(self, request, tracking_id):
        student = get_object_or_404(Student, tracking_id=tracking_id)
        if not student.otp_locked:
            return redirect(reverse('portal:landing', kwargs={'tracking_id': tracking_id}))
        return render(request, 'portal/locked.html', {'student': student})
