import os
import sys
import json
import base64
import requests
import django

# Add tap_project to sys.path
sys.path.append('.')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tap_project.settings")
django.setup()

from django.conf import settings
from m2p.crypto import M2PCryptoHelper

# Base URL permutations
urls = [
    "https://ssltest.yappay.in/Yappay/kyc/customer/generate/otp",
    "https://ssltest.yappay.in/kyc/customer/generate/otp"
]

# We will test entity_keys and headers
entity_keys = ["TRANSCORP", "TRANSCORPMIN"]
tenants = ["TRANSCORP", "TRANSCORPMIN"]
business_types = ["TCAPAAR", "TCASPAAR"]

# Mobile number to use for test
mobile = "9045781643"
apaar_id = "APAAR-E2E-1782218588"

for url in urls:
    for entity_key in entity_keys:
        for tenant in tenants:
            for biz_type in business_types:
                # Build partnerToken based on tenant
                partner_token = base64.b64encode(tenant.encode('utf-8')).decode('utf-8')
                
                header_sets = [
                    # Header Set 1 (Basic)
                    {
                        "TENANT": tenant,
                        "Content-Type": "application/json"
                    },
                    # Header Set 2 (Partner Auth)
                    {
                        "TENANT": tenant,
                        "Content-Type": "application/json",
                        "partnerId": tenant,
                        "partnerToken": f"Basic {partner_token}"
                    }
                ]
                
                for idx, headers in enumerate(header_sets):
                    # Set settings variable for entity key
                    settings.M2P_ENTITY_KEY = entity_key
                    crypto = M2PCryptoHelper()
                    
                    payload = {
                        "entityId": apaar_id,
                        "mobileNumber": f"+91{mobile}",
                        "businessType": biz_type,
                        "entityType": "CUSTOMER"
                    }
                    
                    try:
                        plain_json_str = json.dumps(payload)
                        encrypted_payload = crypto.encrypt_request(plain_json_str)
                        
                        resp = requests.post(url, json=encrypted_payload, headers=headers, timeout=10)
                        
                        # Check if response is successful or contains decryption info
                        status_code = resp.status_code
                        resp_json = None
                        try:
                            resp_json = resp.json()
                        except:
                            resp_json = resp.text[:200]
                            
                        # If status code is 200, or if we get a response that isn't a standard 500 error
                        # Let's print details
                        is_interesting = status_code == 200 or (isinstance(resp_json, dict) and "exception" in resp_json)
                        
                        print(f"URL: {url} | EntityKey: {entity_key} | Tenant: {tenant} | Headers: {idx+1} | BizType: {biz_type} => Status: {status_code}")
                        if is_interesting:
                            print(f"  --> Response: {resp_json}")
                            if status_code == 200 and isinstance(resp_json, dict):
                                try:
                                    decrypted = crypto.decrypt_response(resp_json)
                                    print(f"  --> Decrypted: {decrypted}")
                                except Exception as dec_err:
                                    print(f"  --> Decrypt Error: {dec_err}")
                                    
                    except Exception as e:
                        print(f"Error testing: {e}")
