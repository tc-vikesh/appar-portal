from django.db import models
from applications.models import Student

class WebhookLog(models.Model):
    WEBHOOK_TYPE_CHOICES = [
        ('inbound_twa_app_status', 'inbound_twa_app_status'),
        ('inbound_twa_kyc_status', 'inbound_twa_kyc_status'),
        ('outbound_abc_app_status', 'outbound_abc_app_status'),
        ('outbound_abc_kyc_status', 'outbound_abc_kyc_status'),
    ]

    DIRECTION_CHOICES = [
        ('inbound', 'inbound'),
        ('outbound', 'outbound'),
    ]

    id = models.BigAutoField(primary_key=True)
    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_logs'
    )
    tracking_id = models.CharField(max_length=50, db_index=True)
    webhook_type = models.CharField(max_length=30, choices=WEBHOOK_TYPE_CHOICES)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    endpoint = models.CharField(max_length=500)
    payload = models.JSONField(null=True, blank=True)
    encrypted_payload = models.JSONField(null=True, blank=True)
    source_ip = models.CharField(max_length=45, null=True, blank=True) # inbound only
    ip_whitelisted = models.BooleanField(null=True, blank=True) # inbound only
    http_status = models.SmallIntegerField(null=True, blank=True)
    success = models.BooleanField()
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.SmallIntegerField(default=0)
    max_retries = models.SmallIntegerField(default=3)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'webhook_logs'
        indexes = [
            models.Index(fields=['next_retry_at']),
        ]

    def __str__(self):
        return f"Webhook | {self.webhook_type} | Success: {self.success}"
