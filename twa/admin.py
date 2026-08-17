from django.contrib import admin
from twa.models import TWAApiLog

@admin.register(TWAApiLog)
class TWAApiLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'student',
        'tracking_id',
        'endpoint',
        'http_status',
        'success',
        'created_at'
    )
    list_filter = ('success', 'http_status', 'endpoint')
    search_fields = ('tracking_id', 'apaar_id', 'request_url')
    readonly_fields = (
        'id',
        'student',
        'apaar_id',
        'tracking_id',
        'endpoint',
        'request_url',
        'request_payload',
        'encrypted_request_payload',
        'http_status',
        'response_payload',
        'encrypted_response_payload',
        'success',
        'error_message',
        'duration_ms',
        'created_at'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
