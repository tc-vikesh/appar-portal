from django.test import TestCase
from django.conf import settings
from m2p.crypto import M2PCryptoHelper
from m2p.client import M2PClient
from m2p.models import M2PApiLog
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

class M2PCryptoTestCase(TestCase):
    def setUp(self):
        # Generate ephemeral keys for testing to avoid relying on external files
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        self.public_key = self.private_key.public_key()

        # Serialize keys to PEM format
        self.private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(b"testpass")
        )
        self.public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        # Initialize helper with ephemeral keys
        self.helper = M2PCryptoHelper(
            public_key_pem=self.public_pem,
            private_key_pem=self.private_pem,
            private_key_passphrase="testpass"
        )

    def test_aes_encryption_decryption(self):
        plain_text = '{"name": "Vikesh Sharma", "mobile": "9876543210"}'
        key = "1234567890123456"
        iv = "6543210987654321"

        encrypted = self.helper.aes_encrypt(plain_text, key, iv)
        decrypted = self.helper.aes_decrypt(encrypted, key.encode('utf-8'), iv)

        self.assertEqual(decrypted, plain_text)

    def test_rsa_key_encryption_decryption(self):
        session_key = "9999888877776666"
        encrypted_key = self.helper.encrypt_key(session_key)
        decrypted_key_bytes = self.helper.decrypt_session_key(encrypted_key)
        
        self.assertEqual(decrypted_key_bytes.decode('utf-8'), session_key)

    def test_signature_verification(self):
        payload = '{"test": "data"}'
        signature = self.helper.sign_json(payload)
        
        # Verify using standard cryptography public key verifier
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        
        sig_bytes = base64.b64decode(signature)
        self.public_key.verify(
            sig_bytes,
            payload.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA1()
        ) # Will raise exception if invalid

    def test_encrypt_decrypt_envelope(self):
        plain_payload = '{"amount": 100, "currency": "INR"}'
        envelope = self.helper.encrypt_request(plain_payload)
        
        self.assertIn("body", envelope)
        self.assertIn("token", envelope)
        self.assertIn("key", envelope)
        self.assertIn("entity", envelope)
        self.assertIn("refNo", envelope)

        # Wrap in expected response format (with headers)
        response_envelope = {
            "body": envelope["body"],
            "headers": {
                "key": envelope["key"],
                "refNo": envelope["refNo"],
                "entity": envelope["entity"],
                "hash": envelope["token"]
            }
        }

        decrypted_payload = self.helper.decrypt_response(response_envelope)
        self.assertEqual(decrypted_payload, plain_payload)


from unittest.mock import patch, MagicMock

class M2PClientFlowTestCase(TestCase):
    def setUp(self):
        # Generate ephemeral keys for testing to avoid relying on external files
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        self.public_key = self.private_key.public_key()

        # Serialize keys to PEM format
        self.private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(b"testpass")
        )
        self.public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        # Setup mock settings paths
        import tempfile
        self.pub_file = tempfile.NamedTemporaryFile(delete=False)
        self.pub_file.write(self.public_pem)
        self.pub_file.close()

        self.priv_file = tempfile.NamedTemporaryFile(delete=False)
        self.priv_file.write(self.private_pem)
        self.priv_file.close()

        # Store original settings to restore later
        self.orig_encryption_enabled = getattr(settings, 'M2P_ENCRYPTION_ENABLED', False)
        self.orig_pub_path = getattr(settings, 'M2P_PUBLIC_KEY_PATH', '')
        self.orig_priv_path = getattr(settings, 'TRANSCORP_PRIVATE_KEY_PATH', '')
        self.orig_passphrase = getattr(settings, 'TRANSCORP_PRIVATE_KEY_PASSPHRASE', '')
        self.orig_entity_key = getattr(settings, 'M2P_ENTITY_KEY', '')

        settings.M2P_PUBLIC_KEY_PATH = self.pub_file.name
        settings.TRANSCORP_PRIVATE_KEY_PATH = self.priv_file.name
        settings.TRANSCORP_PRIVATE_KEY_PASSPHRASE = "testpass"
        settings.M2P_ENTITY_KEY = "TRANSCORP"

        from applications.models import Student
        self.student = Student.objects.create(
            tracking_id="TAP-MOCK-M2P-FLOW",
            apaar_id="APAAR-M2P-FLOW",
            full_name="Vikesh M2P Flow",
            dob="2000-01-01",
            gender="M",
            mobile="9876543210"
        )

    def tearDown(self):
        import os
        try:
            os.unlink(self.pub_file.name)
        except OSError:
            pass
        try:
            os.unlink(self.priv_file.name)
        except OSError:
            pass
        
        # Restore settings
        settings.M2P_ENCRYPTION_ENABLED = self.orig_encryption_enabled
        settings.M2P_PUBLIC_KEY_PATH = self.orig_pub_path
        settings.TRANSCORP_PRIVATE_KEY_PATH = self.orig_priv_path
        settings.TRANSCORP_PRIVATE_KEY_PASSPHRASE = self.orig_passphrase
        settings.M2P_ENTITY_KEY = self.orig_entity_key
        
        self.student.delete()

    @patch('requests.post')
    def test_m2p_client_flow_without_encryption(self, mock_post):
        settings.M2P_ENCRYPTION_ENABLED = False
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "result": {"success": True}}
        mock_post.return_value = mock_resp
        
        client = M2PClient()
        response = client.generate_otp(self.student)
        
        self.assertTrue(response["success"])
        
        # Verify mock post was called with plain text payload
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["entityId"], "APAAR-M2P-FLOW")
        
        # Verify db log contains plain payload but no encrypted payloads
        log = M2PApiLog.objects.filter(student=self.student, endpoint="generate_otp").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.request_payload["entityId"], "APAAR-M2P-FLOW")
        self.assertIsNone(log.encrypted_request_payload)
        self.assertIsNone(log.encrypted_response_payload)

    @patch('requests.post')
    def test_m2p_client_flow_with_encryption(self, mock_post):
        settings.M2P_ENCRYPTION_ENABLED = True
        
        # We need to construct a valid encrypted mock response envelope
        # Let's use our helper to encrypt a mock response payload
        from m2p.crypto import M2PCryptoHelper
        helper = M2PCryptoHelper(
            public_key_pem=self.public_pem,
            private_key_pem=self.private_pem,
            private_key_passphrase="testpass"
        )
        
        plain_response = '{"success": true, "result": {"success": true, "message": "Encrypted successful call"}}'
        envelope = helper.encrypt_request(plain_response)
        
        mock_resp_envelope = {
            "body": envelope["body"],
            "headers": {
                "key": envelope["key"],
                "refNo": envelope["refNo"],
                "entity": envelope["entity"],
                "hash": envelope["token"]
            }
        }
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_resp_envelope
        mock_post.return_value = mock_resp
        
        client = M2PClient()
        response = client.generate_otp(self.student)
        
        self.assertTrue(response["success"])
        
        # Verify mock post was called with encrypted request wrapper
        args, kwargs = mock_post.call_args
        req_json = kwargs["json"]
        self.assertIn("body", req_json)
        self.assertIn("key", req_json)
        self.assertIn("token", req_json)
        
        # Verify database log contains BOTH plain and encrypted payloads
        log = M2PApiLog.objects.filter(student=self.student, endpoint="generate_otp").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.request_payload["entityId"], "APAAR-M2P-FLOW")
        self.assertIsNotNone(log.encrypted_request_payload)
        self.assertEqual(log.encrypted_request_payload["body"], req_json["body"])
        self.assertEqual(log.response_payload["result"]["message"], "Encrypted successful call")
        self.assertIsNotNone(log.encrypted_response_payload)
        self.assertEqual(log.encrypted_response_payload["body"], envelope["body"])

