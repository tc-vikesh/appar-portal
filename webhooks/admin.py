from django.contrib import admin
from webhooks.models import WebhookLog

@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'student',
        'tracking_id',
        'webhook_type',
        'direction',
        'http_status',
        'success',
        'created_at'
    )
    list_filter = (
        'direction',
        'webhook_type',
        'success',
        'http_status',
        'ip_whitelisted'
    )
    search_fields = ('tracking_id', 'source_ip', 'endpoint')
    readonly_fields = (
        'id',
        'student',
        'tracking_id',
        'webhook_type',
        'direction',
        'endpoint',
        'payload',
        'source_ip',
        'ip_whitelisted',
        'http_status',
        'success',
        'error_message',
        'retry_count',
        'max_retries',
        'next_retry_at',
        'delivered_at',
        'created_at'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
