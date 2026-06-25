import os
import sys
import json
import django
import requests

# Add tap_project to sys.path
sys.path.append('.')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tap_project.settings")
django.setup()

from django.conf import settings
from applications.models import Student
from m2p.crypto import M2PCryptoHelper

# Configure settings
settings.M2P_ENCRYPTION_ENABLED = True
settings.M2P_BASE_URL = 'https://ssltest.yappay.in/Yappay'

student = Student.objects.first()
if not student:
    student = Student.objects.create(
        tracking_id="TAP-TEST-M2P-LIVE",
        apaar_id="Vikes81643",
        full_name="Vikesh Sharma",
        dob="2000-01-01",
        gender="M",
        mobile="9045781643",
        email="vksharma7664@gmail.com"
    )

print(f"Using Student: {student.apaar_id}, Mobile: {student.mobile}")

crypto = M2PCryptoHelper()

# Payloads to test
payloads = {
    "Payload 1 (Plaintext UAT default)": {
        "entityId": student.apaar_id,
        "mobileNumber": f"+91{student.mobile}",
        "businessType": "TCAPAAR",
        "entityType": "CUSTOMER"
    },
    "Payload 2 (Java main method format)": {
        "entityId": student.mobile
    },
    "Payload 3 (Java main method format with +91)": {
        "entityId": f"+91{student.mobile}"
    },
    "Payload 4 (APAAR ID only)": {
        "entityId": student.apaar_id
    }
}

# Header sets to test
header_sets = {
    "Headers Set 1 (Basic)": {
        "TENANT": "TRANSCORP",
        "Content-Type": "application/json"
    },
    "Headers Set 2 (Full PHP-style)": {
        "TENANT": "TRANSCORP",
        "Content-Type": "application/json",
        "partnerId": "TRANSCORP",
        "partnerToken": "Basic VFJBTlNDT1JQ"
    }
}

url = 'https://ssltest.yappay.in/Yappay/kyc/customer/generate/otp'

for payload_desc, payload in payloads.items():
    for headers_desc, headers in header_sets.items():
        print(f"\n--- Testing with {payload_desc} and {headers_desc} ---")
        try:
            plain_json_str = json.dumps(payload)
            encrypted_payload = crypto.encrypt_request(plain_json_str)
            
            resp = requests.post(url, json=encrypted_payload, headers=headers, timeout=10)
            print(f"HTTP Status: {resp.status_code}")
            try:
                resp_json = resp.json()
                print("Response JSON:", resp_json)
                if resp.status_code == 200:
                    # Try decrypting
                    try:
                        decrypted = crypto.decrypt_response(resp_json)
                        print("Decrypted body:", decrypted)
                    except Exception as dec_err:
                        print("Decryption failed:", dec_err)
            except Exception as parse_err:
                print("Raw Response text:", resp.text[:500])
        except Exception as run_err:
            print("Run Error:", run_err)
