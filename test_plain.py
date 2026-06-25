import requests
import json

url = "https://kycuat.yappay.in/kyc/customer/generate/otp"

payloads = [
    {
        "entityId": "APAAR-E2E-1782218588",
        "mobileNumber": "+919045781643",
        "businessType": "TCAPAAR",
        "entityType": "CUSTOMER"
    },
    {
        "entityId": "APAAR-E2E-1782218588",
        "mobileNumber": "+919045781643",
        "businessType": "TCASPAAR",
        "entityType": "CUSTOMER"
    },
    {
        "entityId": "APAAR-E2E-1782218588",
        "mobileNumber": "+919045781643",
        "businessType": "TRANSCORP",
        "entityType": "CUSTOMER"
    },
    {
        "entityId": "APAAR-E2E-1782218588",
        "mobileNumber": "+919045781643",
        "businessType": "TRANSCORPMIN",
        "entityType": "CUSTOMER"
    }
]

tenants = ["TRANSCORP", "TRANSCORPMIN"]

for tenant in tenants:
    for payload in payloads:
        headers = {
            "TENANT": tenant,
            "Content-Type": "application/json"
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            print(f"Tenant: {tenant} | BizType: {payload['businessType']} => Status: {resp.status_code}")
            print(f"  Response: {resp.text[:500]}")
        except Exception as e:
            print(f"Error for tenant {tenant}: {e}")
