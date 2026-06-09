import time
import requests
from django.conf import settings
from m2p.models import M2PApiLog

class M2PClient:
    """
    Client for interacting with the M2P KYC UAT endpoints.
    Per Constitution P1, all outbound calls are logged in M2PApiLog inside a finally block.
    Per Constitution P6, all sensitive data (OTPs and PANs) is masked in database log columns.
    """
    def __init__(self):
        self.base_url = getattr(settings, 'M2P_BASE_URL', 'https://kycuat.yappay.in')
        self.tenant = getattr(settings, 'M2P_TENANT', 'TRANSCORP')

    def _mask_payload(self, payload):
        """Safely masks PAN numbers and OTPs in request/response payloads for logging (Constitution P6)."""
        if not isinstance(payload, dict):
            return payload

        masked = payload.copy()
        if 'otp' in masked:
            masked['otp'] = '***'

        if 'kycInfo' in masked and isinstance(masked['kycInfo'], list):
            masked_kyc = []
            for doc in masked['kycInfo']:
                if isinstance(doc, dict):
                    doc_copy = doc.copy()
                    if 'documentNo' in doc_copy:
                        doc_copy['documentNo'] = '***'
                    masked_kyc.append(doc_copy)
                else:
                    masked_kyc.append(doc)
            masked['kycInfo'] = masked_kyc

        return masked

    def generate_otp(self, student):
        """
        POST /kyc/customer/generate/otp
        Calls M2P generate-OTP service.
        """
        url = f"{self.base_url}/kyc/customer/generate/otp"
        headers = {
            "TENANT": self.tenant,
            "Content-Type": "application/json"
        }
        request_payload = {
            "entityId": student.apaar_id,
            "mobileNumber": f"+91{student.mobile}",
            "businessType": "TCASPAAR",
            "entityType": "CUSTOMER"
        }

        # Initialize log object BEFORE network call (api-logging-pattern SKILL.md)
        log = M2PApiLog(
            student=student,
            apaar_id=student.apaar_id,
            endpoint="generate_otp",
            request_url=url,
            request_headers=headers,
        )

        start = time.monotonic()
        try:
            # UAT plain text call (as per context decision)
            response = requests.post(url, json=request_payload, headers=headers, timeout=15)
            log.http_status = response.status_code

            try:
                body = response.json()
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            log.response_payload = self._mask_payload(body)

            # Determine success based on response schema
            # Example M2P success: 200 OK with success indicator in body
            success = (
                response.status_code == 200 and 
                (body.get("result", {}).get("success") is True or body.get("success") is True)
            )
            log.success = success
            if not success:
                log.error_message = body.get("exception") or body.get("error") or "Non-success M2P response"

            return body

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {exc}"
            raise exc

        finally:
            # Measure duration and complete audit save in finally block (Constitution P1)
            log.request_payload = self._mask_payload(request_payload)
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save()

    def register_min_kyc(self, student, otp, pan_number):
        """
        POST /kyc/v2/register
        Registers customer (MIN KYC) using OTP and PAN number.
        """
        url = f"{self.base_url}/kyc/v2/register"
        headers = {
            "TENANT": self.tenant,
            "Content-Type": "application/json"
        }
        
        # Parse full_name into first, middle, last name blocks
        full_name = student.full_name or ""
        name_parts = full_name.split()
        first_name = name_parts[0] if name_parts else "Student"
        last_name = name_parts[-1] if len(name_parts) > 1 else ""
        middle_name = " ".join(name_parts[1:-1]) if len(name_parts) > 2 else ""

        # Map student gender category to M2P categories
        gender_map = {'M': 'MALE', 'F': 'FEMALE', 'O': 'OTHER'}
        mapped_gender = gender_map.get(student.gender, 'MALE')

        # Address details extraction
        perm = student.permanent_address or {}
        curr = student.current_address or {}
        
        perm_addr1 = perm.get('address1') or perm.get('address_line') or "Address Line 1"
        perm_addr2 = perm.get('address2') or ""
        perm_addr3 = perm.get('address3') or ""
        perm_city = perm.get('city') or "Mumbai"
        perm_state = perm.get('state') or "Maharashtra"
        perm_pincode = perm.get('pincode') or perm.get('pin_code') or "400001"

        curr_addr1 = curr.get('address1') or curr.get('address_line') or perm_addr1
        curr_addr2 = curr.get('address2') or ""
        curr_addr3 = curr.get('address3') or ""
        curr_city = curr.get('city') or perm_city
        curr_state = curr.get('state') or perm_state
        curr_pincode = curr.get('pincode') or curr.get('pin_code') or perm_pincode

        request_payload = {
            "entityId": student.apaar_id,
            "channelName": "MIN_KYC",
            "entityType": "CUSTOMER",
            "businessType": "TCAPAAR",
            "businessId": student.apaar_id,
            "title": student.title or ("Mr" if student.gender == "M" else "Ms"),
            "otp": otp,
            "firstName": first_name,
            "middleName": middle_name,
            "lastName": last_name,
            "gender": mapped_gender,
            "isNRICustomer": False,
            "isMinor": False,
            "isDependant": False,
            "maritalStatus": "SINGLE",
            "countryCode": "91",
            "employmentIndustry": "INFORMATION_TECHNOLOGY",
            "employmentType": "EMPLOYED",
            "plasticCode": "TYPE1",
            "addressInfo": [
                {
                    "addressCategory": "PERMANENT",
                    "address1": perm_addr1,
                    "address2": perm_addr2,
                    "address3": perm_addr3,
                    "city": perm_city,
                    "state": perm_state,
                    "country": "INDIA",
                    "pinCode": str(perm_pincode)
                },
                {
                    "addressCategory": "COMMUNICATION",
                    "address1": curr_addr1,
                    "address2": curr_addr2,
                    "address3": curr_addr3,
                    "city": curr_city,
                    "state": curr_state,
                    "country": "INDIA",
                    "pinCode": str(curr_pincode)
                }
            ],
            "communicationInfo": [
                {
                    "contactNo": f"+91{student.mobile}",
                    "notification": True,
                    "emailId": student.email
                }
            ],
            "kitInfo": [
                {
                    "cardType": "VIRTUAL",
                    "cardCategory": "PREPAID",
                    "cardRegStatus": "ACTIVE",
                    "aliasName": first_name
                }
            ],
            "kycInfo": [
                {
                    "documentType": "PAN",
                    "documentNo": pan_number,
                    "documentExpiry": "2099-03-01"
                }
            ],
            "dateInfo": [
                {
                    "dateType": "DOB",
                    "date": str(student.dob) if student.dob else "2000-01-01"
                }
            ]
        }

        # Initialize log object BEFORE network call (api-logging-pattern SKILL.md)
        log = M2PApiLog(
            student=student,
            apaar_id=student.apaar_id,
            endpoint="register",
            request_url=url,
            request_headers=headers,
        )

        start = time.monotonic()
        try:
            # UAT plain text call (as per context decision)
            response = requests.post(url, json=request_payload, headers=headers, timeout=15)
            log.http_status = response.status_code

            try:
                body = response.json()
            except ValueError:
                body = {"_raw_body": response.text[:1000]}

            log.response_payload = self._mask_payload(body)

            success = (
                response.status_code == 200 and
                not body.get("exception") and
                (
                    body.get("success") is True or
                    body.get("result", {}).get("success") is True or
                    (body.get("result") is not None and (
                        "kitNo" in body["result"] or 
                        "token" in body["result"] or 
                        "entityId" in body["result"]
                    ))
                )
            )
            log.success = success
            if not success:
                log.error_message = body.get("exception") or body.get("error") or "Non-success M2P response"

            return body

        except Exception as exc:
            log.success = False
            log.error_message = f"{type(exc).__name__}: {exc}"
            raise exc

        finally:
            # Measure duration and complete audit save in finally block (Constitution P1)
            log.request_payload = self._mask_payload(request_payload)
            log.duration_ms = int((time.monotonic() - start) * 1000)
            log.save()
