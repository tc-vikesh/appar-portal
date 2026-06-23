from django.db import models
from applications.models import Student

class M2PApiLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='m2p_logs'
    )
    apaar_id = models.CharField(max_length=50, db_index=True)
    endpoint = models.CharField(max_length=50) # 'generate_otp' or 'register'
    request_url = models.CharField(max_length=500)
    request_headers = models.JSONField(null=True, blank=True)
    request_payload = models.JSONField(null=True, blank=True)
    encrypted_request_payload = models.JSONField(null=True, blank=True)
    http_status = models.SmallIntegerField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    encrypted_response_payload = models.JSONField(null=True, blank=True)
    success = models.BooleanField()
    error_message = models.TextField(null=True, blank=True)
    duration_ms = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'm2p_api_logs'
        indexes = [
            models.Index(fields=['student']),
            models.Index(fields=['apaar_id']),
            models.Index(fields=['endpoint', 'success']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"M2P | {self.endpoint} | Success: {self.success}"
