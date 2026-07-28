import json
import base64
import os
from django.conf import settings
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.serializers.json import DjangoJSONEncoder

def encrypt_abc_payload(payload_dict):
    """
    Encrypts a dictionary payload into AES-256-GCM format as required by ABC.
    Returns a dictionary {"encryptedData": "<base64_encoded_string>"}
    """
    key_b64 = getattr(settings, 'ABC_ENCRYPTION_KEY', None)
    if not key_b64:
        raise ValueError("ABC_ENCRYPTION_KEY is not set in settings.")
    
    key = base64.b64decode(key_b64)
    
    # 1. Generate a 12-byte IV (Initialization Vector).
    iv = os.urandom(12)
    
    # Serialize JSON with Django's encoder for safety
    json_string = json.dumps(payload_dict, cls=DjangoJSONEncoder)
    
    # 2. Encrypt the JSON payload using AES-256-GCM with the 32-byte key.
    # cryptography's AESGCM automatically appends the 16-byte authentication tag
    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(iv, json_string.encode('utf-8'), None)
    
    # 3. Combine: IV + CipherText + Authentication Tag
    combined = iv + ciphertext_and_tag
    
    # 4. Encode the combined result in Base64
    encrypted_b64 = base64.b64encode(combined).decode('utf-8')
    
    return {"encryptedData": encrypted_b64}


def decrypt_abc_payload(encrypted_b64):
    """
    Decrypts the ABC AES-256-GCM base64 encoded payload.
    Returns the decrypted JSON as a dictionary.
    """
    key_b64 = getattr(settings, 'ABC_ENCRYPTION_KEY', None)
    if not key_b64:
        raise ValueError("ABC_ENCRYPTION_KEY is not set in settings.")
    
    key = base64.b64decode(key_b64)
    combined = base64.b64decode(encrypted_b64)
    
    # Extract IV (first 12 bytes) and ciphertext_and_tag (rest)
    iv = combined[:12]
    ciphertext_and_tag = combined[12:]
    
    # Decrypt
    aesgcm = AESGCM(key)
    decrypted_bytes = aesgcm.decrypt(iv, ciphertext_and_tag, None)
    
    return json.loads(decrypted_bytes.decode('utf-8'))
