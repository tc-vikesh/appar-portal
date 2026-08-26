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
from webhooks.dispatcher import ABCWebhookDispatcher

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
        consent_aadhaar_ovd = body.get('consent_aadhaar_ovd') is True
        consent_ckycr = body.get('consent_ckycr') is True
        consent_address_mismatch = body.get('consent_address_mismatch') is True
        consent_kyc_ppi = body.get('consent_kyc_ppi') is True
        consent_terms_conditions = body.get('consent_terms_conditions') is True

        if not (consent_aadhaar_ovd and consent_ckycr and consent_address_mismatch and consent_kyc_ppi and consent_terms_conditions):
            return JsonResponse({"success": False, "error": "You must accept all 5 terms and provide all consents to proceed."}, status=400)

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
                # Save aadhaar number, ref_id, and consents on student record
                student.aadhaar_number = aadhaar_number
                student.aadhaar_ref_id = ref_id
                student.consent_aadhaar_ovd = consent_aadhaar_ovd
                student.consent_ckycr = consent_ckycr
                student.consent_address_mismatch = consent_address_mismatch
                student.consent_kyc_ppi = consent_kyc_ppi
                student.consent_terms_conditions = consent_terms_conditions
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

                # Trigger ABC webhook for KYC status update
                dispatcher = ABCWebhookDispatcher()
                dispatcher.dispatch_kyc_status_update(student, "Completed MIN KYC")

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

        masked_mobile = f"XXXXXX{student.mobile[-4:]}" if student.mobile and len(student.mobile) >= 4 else "XXXXXX1643"

        default_content = """<p>Please note: As per RBI's Master Direction on PPIs, a Small PPI carries reduced limits on loading, balance and usage, and cannot be used for cash withdrawal. To enhance your limits and unlock full features, please complete Full KYC.</p>
<h4>Next Steps:</h4>
<ol>
    <li>Download the Transcorp Web App (TransWallet)- through link below.</li>
    <li>Log in using your registered mobile number <strong>+91 {mobile}</strong>.</li>
    <li>Complete Full KYC / Video-based Customer Identification Process (V-CIP) to upgrade to a Full-KYC PPI and enhance your transaction limits.</li>
    <li>Track your physical card delivery status inside the Profile Section of app.</li>
    <li>Manage your card —set/modify limits, block/ unblock, or request replacement — from the "Profile &gt; Manage Card" section.</li>
    <li>View balance and Transaction history or download statement to track your expenses.</li>
    <li>Read the full Terms &amp; Conditions before first use.</li>
    <li>For queries or complaints, use the in-app Customer Care section.</li>
</ol>
<p><em>This PPI is issued by Transcorp International Limited, authorised by the Reserve Bank of India, and is subject to RBI's Master Directions on Prepaid Payment Instruments.</em></p>"""

        # Load success page CMS content
        cms_page, created = CMSPage.objects.get_or_create(
            slug='success-page',
            defaults={
                'title': 'Registration Successful!',
                'content': default_content
            }
        )

        # Force update content in case it already exists in the DB with the old text
        if cms_page.content != default_content:
            cms_page.content = default_content
            cms_page.title = 'Registration Successful!'
            cms_page.save()

        # Replace the placeholder dynamically for the current student
        dynamic_content = cms_page.content.replace('{mobile}', masked_mobile)

        return render(request, 'portal/success.html', {
            'student': student,
            'cms_page': cms_page,
            'dynamic_content': dynamic_content
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


class M2PCryptoTestView(View):
    """
    GET /portal/test-m2p-crypto/
    Diagnostic view to run side-by-side comparison of Python and Node encryption,
    and to test live endpoint connections for all permutations.
    """
    def get(self, request):
        import subprocess
        import json
        import requests
        from django.conf import settings
        from m2p.crypto import M2PCryptoHelper
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        student = Student.objects.first()
        if not student:
            student_apaar_id = "Vikes81643"
            student_mobile = "9045781643"
        else:
            student_apaar_id = student.apaar_id
            student_mobile = student.mobile

        # Fixed key and IV to compare raw ciphertext values
        fixed_key = "1234123412341278"
        fixed_iv = "1234123412341279"

        # Payloads to test
        payloads = {
            "Payload_1_Standard_UAT_Plaintext_Format": {
                "entityId": student_apaar_id,
                "mobileNumber": f"+91{student_mobile}",
                "businessType": "TCAPAAR",
                "entityType": "CUSTOMER"
            },
            "Payload_2_Java_Mobile_Only": {
                "entityId": student_mobile
            },
            "Payload_3_Java_Mobile_With_Country_Code": {
                "entityId": f"+91{student_mobile}"
            },
            "Payload_4_Apaar_ID_Only": {
                "entityId": student_apaar_id
            }
        }

        crypto = M2PCryptoHelper()
        
        # 1. Run side-by-side comparison for Payload 1
        test_payload_str = json.dumps(payloads["Payload_1_Standard_UAT_Plaintext_Format"])
        
        try:
            node_res = subprocess.run(
                ["node", "test_crypto.js", test_payload_str, fixed_key, fixed_iv],
                capture_output=True,
                text=True,
                check=True
            )
            node_json = json.loads(node_res.stdout)
        except Exception as e:
            node_json = {"error": f"Node.js failed: {str(e)}", "stderr": getattr(e, 'stderr', '')}

        try:
            py_body = crypto.aes_encrypt(test_payload_str, fixed_key, fixed_iv)
            py_key = crypto.encrypt_key(fixed_key)
            py_token = crypto.sign_json(test_payload_str)
            py_entity = crypto.encrypt_key("TRANSCORP")
            
            # Verify signatures using business public key
            import base64
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
            
            node_token = node_json.get("token", "")
            node_sig_ok = False
            node_sig_err = None
            if node_token:
                try:
                    with open("D:\\Apaar\\M2P_keys\\node\\prepaid.transcorpint.com.openssl", "rb") as f:
                        bus_pub_key = load_der_public_key(f.read())
                    bus_pub_key.verify(
                        base64.b64decode(node_token),
                        test_payload_str.encode('utf-8'),
                        asymmetric_padding.PKCS1v15(),
                        hashes.SHA1()
                    )
                    node_sig_ok = True
                except Exception as ve:
                    node_sig_err = str(ve)
                    
            py_sig_ok = False
            py_sig_err = None
            if py_token:
                try:
                    with open("D:\\Apaar\\M2P_keys\\node\\prepaid.transcorpint.com.openssl", "rb") as f:
                        bus_pub_key = load_der_public_key(f.read())
                    bus_pub_key.verify(
                        base64.b64decode(py_token),
                        test_payload_str.encode('utf-8'),
                        asymmetric_padding.PKCS1v15(),
                        hashes.SHA1()
                    )
                    py_sig_ok = True
                except Exception as ve:
                    py_sig_err = str(ve)

            py_json = {
                "body": py_body,
                "key": py_key,
                "token": py_token,
                "entity": py_entity,
                "refNo": fixed_iv,
                "sig_ok": py_sig_ok,
                "sig_error": py_sig_err
            }
        except Exception as e:
            py_json = {"error": f"Python encryption failed: {str(e)}"}
            node_sig_ok = False
            node_sig_err = None

        comparisons = {
            "aes_ciphertext_match": py_json.get("body") == node_json.get("body") if "body" in node_json else False,
            "signature_match": py_json.get("token") == node_json.get("token") if "token" in node_json else False,
            "node_signature_verified_by_python_pubkey": node_sig_ok,
            "node_signature_error": node_sig_err
        }

        live_results = []

        # Header sets to try
        header_sets = {
            "Headers_Basic": {
                "TENANT": "TRANSCORP",
                "Content-Type": "application/json"
            },
            "Headers_With_Partner_Auth": {
                "TENANT": "TRANSCORP",
                "Content-Type": "application/json",
                "partnerId": "TRANSCORP",
                "partnerToken": "Basic VFJBTlNDT1JQ"
            },
            "Headers_With_Partner_And_Basic_Auth": {
                "TENANT": "TRANSCORP",
                "Content-Type": "application/json",
                "partnerId": "TRANSCORP",
                "partnerToken": "Basic VFJBTlNDT1JQ",
                "Authorization": "Basic YWRtaW46YWRtaW4="
            }
        }

        # 2. Run Live Calls for all permutations
        live_results = []
        
        # Test permutations for generate_otp
        urls_otp = [
            "https://ssltest.yappay.in/Yappay/kyc/customer/generate/otp",
            "https://ssltest.yappay.in/kyc/customer/generate/otp"
        ]
        
        # Test permutations for register
        urls_register = [
            "https://ssltest.yappay.in/Yappay/kyc/v2/register",
            "https://ssltest.yappay.in/kyc/v2/register"
        ]

        # Fetch a dummy register request payload
        with open("d:\\Apaar\\Dev\\codebase\\m2p_register_request.json", "r") as f:
            register_payload = json.load(f)

        entity_keys = ["TRANSCORP", "TRANSCORPMIN", "MSWIPE"]

        for url in urls_otp:
            for p_name, payload in payloads.items():
                for h_name, headers in header_sets.items():
                    for ek in entity_keys:
                        test_case_name = f"OTP | URL: {url} | Payload: {p_name} | Headers: {h_name} | Entity: {ek}"
                        try:
                            # Override entity key in settings
                            settings.M2P_ENTITY_KEY = ek
                            crypto = M2PCryptoHelper()
                            
                            plain_json_str = json.dumps(payload)
                            encrypted_payload = crypto.encrypt_request(plain_json_str)
                            resp = requests.post(url, json=encrypted_payload, headers=headers, timeout=10)
                            
                            resp_json = None
                            try:
                                resp_json = resp.json()
                            except ValueError:
                                resp_json = {"_raw_text": resp.text[:500]}

                            decrypted_body = None
                            decryption_success = False
                            decryption_error = None
                            
                            if resp.status_code == 200 and resp_json:
                                try:
                                    decrypted_body = crypto.decrypt_response(resp_json)
                                    decryption_success = True
                                except Exception as dec_err:
                                    decryption_error = str(dec_err)

                            live_results.append({
                                "test_case": test_case_name,
                                "http_status": resp.status_code,
                                "raw_response": resp_json,
                                "decrypted_body": decrypted_body,
                                "decryption_success": decryption_success,
                                "decryption_error": decryption_error
                            })
                        except Exception as exc:
                            live_results.append({
                                "test_case": test_case_name,
                                "error": f"{type(exc).__name__}: {str(exc)}"
                            })

        for url in urls_register:
            for h_name, headers in header_sets.items():
                for ek in entity_keys:
                    test_case_name = f"REGISTER | URL: {url} | Headers: {h_name} | Entity: {ek}"
                    try:
                        settings.M2P_ENTITY_KEY = ek
                        crypto = M2PCryptoHelper()
                        
                        plain_json_str = json.dumps(register_payload)
                        encrypted_payload = crypto.encrypt_request(plain_json_str)
                        resp = requests.post(url, json=encrypted_payload, headers=headers, timeout=10)
                        
                        resp_json = None
                        try:
                            resp_json = resp.json()
                        except ValueError:
                            resp_json = {"_raw_text": resp.text[:500]}

                        decrypted_body = None
                        decryption_success = False
                        decryption_error = None
                        
                        if resp.status_code == 200 and resp_json:
                            try:
                                decrypted_body = crypto.decrypt_response(resp_json)
                                decryption_success = True
                            except Exception as dec_err:
                                decryption_error = str(dec_err)

                        live_results.append({
                            "test_case": test_case_name,
                            "http_status": resp.status_code,
                            "raw_response": resp_json,
                            "decrypted_body": decrypted_body,
                            "decryption_success": decryption_success,
                            "decryption_error": decryption_error
                        })
                    except Exception as exc:
                        live_results.append({
                            "test_case": test_case_name,
                            "error": f"{type(exc).__name__}: {str(exc)}"
                        })


        return JsonResponse({
            "status": "completed",
            "node_output": node_json,
            "python_output": py_json,
            "comparisons": comparisons,
            "live_results": live_results
        })




