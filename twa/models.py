from django.db import models
from applications.models import Student

class TWAApiLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='twa_logs'
    )
    apaar_id = models.CharField(max_length=50, db_index=True)
    tracking_id = models.CharField(max_length=50, db_index=True)
    endpoint = models.CharField(max_length=50) # 'sync_onboard' or 'status_pull'
    request_url = models.CharField(max_length=500)
    request_payload = models.JSONField(null=True, blank=True)
    encrypted_request_payload = models.JSONField(null=True, blank=True)
    http_status = models.SmallIntegerField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    encrypted_response_payload = models.JSONField(null=True, blank=True)
    success = models.BooleanField()
    error_message = models.TextField(null=True, blank=True)
    duration_ms = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'twa_api_logs'

    def __str__(self):
        return f"TWA | {self.endpoint} | Success: {self.success}"
