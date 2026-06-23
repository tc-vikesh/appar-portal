import base64
import secrets
import string
from django.conf import settings
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding

class M2PCryptoHelper:
    """
    Cryptographic helper for M2P payload encryption and decryption.
    Replicates Node.js api.js logic in Python using the cryptography library.
    """
    def __init__(self, public_key_pem=None, private_key_pem=None, private_key_passphrase=None):
        self.public_key_pem = public_key_pem
        self.private_key_pem = private_key_pem
        self.private_key_passphrase = private_key_passphrase

        # Retrieve paths and configurations from settings
        self.public_key_path = getattr(settings, 'M2P_PUBLIC_KEY_PATH', '')
        self.private_key_path = getattr(settings, 'TRANSCORP_PRIVATE_KEY_PATH', '')
        self.passphrase_str = private_key_passphrase or getattr(settings, 'TRANSCORP_PRIVATE_KEY_PASSPHRASE', '12345')
        self.entity_key = getattr(settings, 'M2P_ENTITY_KEY', 'TRANSCORP')

    def _get_public_key(self):
        if self.public_key_pem:
            return serialization.load_pem_public_key(self.public_key_pem)
        
        if not self.public_key_path:
            raise ValueError("M2P_PUBLIC_KEY_PATH is not configured in settings.")
        
        with open(self.public_key_path, 'rb') as f:
            return serialization.load_pem_public_key(f.read())

    def _get_private_key(self):
        if self.private_key_pem:
            password = self.private_key_passphrase.encode('utf-8') if self.private_key_passphrase else None
            return serialization.load_pem_private_key(self.private_key_pem, password=password)
        
        if not self.private_key_path:
            raise ValueError("TRANSCORP_PRIVATE_KEY_PATH is not configured in settings.")
        
        password = self.passphrase_str.encode('utf-8') if self.passphrase_str else None
        with open(self.private_key_path, 'rb') as f:
            return serialization.load_pem_private_key(f.read(), password=password)

    def generate_random_16_digits(self) -> str:
        """Generates a random 16-digit string to match the Node.js implementation."""
        return "".join(secrets.choice(string.digits) for _ in range(16))

    def aes_encrypt(self, plain_text: str, key: str, iv: str) -> str:
        """Encrypts plain_text using AES-128-CBC with PKCS7 padding."""
        key_bytes = key.encode('utf-8')
        iv_bytes = iv.encode('utf-8')
        
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(plain_text.encode('utf-8')) + padder.finalize()
        
        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes))
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        
        return base64.b64encode(encrypted_data).decode('utf-8')

    def aes_decrypt(self, cipher_text_base64: str, key_bytes: bytes, iv: str) -> str:
        """Decrypts cipher_text_base64 using AES-128-CBC with PKCS7 padding."""
        encrypted_data = base64.b64decode(cipher_text_base64)
        iv_bytes = iv.encode('utf-8')
        
        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes))
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
        
        unpadder = padding.PKCS7(128).unpadder()
        plain_data = unpadder.update(padded_data) + unpadder.finalize()
        
        return plain_data.decode('utf-8')

    def encrypt_key(self, session_key: str) -> str:
        """Encrypts session_key using M2P's public key with PKCS1 v1.5 padding."""
        pub_key = self._get_public_key()
        encrypted_bytes = pub_key.encrypt(
            session_key.encode('utf-8'),
            asymmetric_padding.PKCS1v15()
        )
        return base64.b64encode(encrypted_bytes).decode('utf-8')

    def decrypt_session_key(self, encrypted_session_key_base64: str) -> bytes:
        """Decrypts session_key using Transcorp's private key with PKCS1 v1.5 padding."""
        priv_key = self._get_private_key()
        encrypted_bytes = base64.b64decode(encrypted_session_key_base64)
        decrypted_bytes = priv_key.decrypt(
            encrypted_bytes,
            asymmetric_padding.PKCS1v15()
        )
        return decrypted_bytes

    def sign_json(self, request_data: str) -> str:
        """Signs the request JSON data using Transcorp's private key with SHA1 hashing and PKCS1 v1.5 padding."""
        priv_key = self._get_private_key()
        signature_bytes = priv_key.sign(
            request_data.encode('utf-8'),
            asymmetric_padding.PKCS1v15(),
            hashes.SHA1()
        )
        return base64.b64encode(signature_bytes).decode('utf-8')

    def encrypt_request(self, plain_json_str: str) -> dict:
        """Encrypts request JSON payload and returns the M2P envelope format."""
        session_key = self.generate_random_16_digits()
        iv = self.generate_random_16_digits()
        
        encrypted_body = self.aes_encrypt(plain_json_str, session_key, iv)
        encrypted_key = self.encrypt_key(session_key)
        token = self.sign_json(plain_json_str)
        entity = self.encrypt_key(self.entity_key)
        
        return {
            "body": encrypted_body,
            "token": token,
            "key": encrypted_key,
            "entity": entity,
            "refNo": iv
        }

    def decrypt_response(self, response_envelope: dict) -> str:
        """Decrypts M2P response envelope and returns the plain JSON string."""
        headers = response_envelope.get("headers") or {}
        encrypted_session_key = headers.get("key")
        iv = headers.get("refNo")
        encrypted_body = response_envelope.get("body")
        
        if not encrypted_session_key or not iv or not encrypted_body:
            raise ValueError("Response envelope is missing key, refNo, or body.")
            
        decrypted_key_bytes = self.decrypt_session_key(encrypted_session_key)
        plain_body = self.aes_decrypt(encrypted_body, decrypted_key_bytes, iv)
        return plain_body
