from django.contrib import admin
from m2p.models import M2PApiLog

@admin.register(M2PApiLog)
class M2PApiLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'student',
        'apaar_id',
        'endpoint',
        'http_status',
        'success',
        'created_at'
    )
    list_filter = ('success', 'http_status', 'endpoint')
    search_fields = ('apaar_id', 'request_url')
    readonly_fields = (
        'id',
        'student',
        'apaar_id',
        'endpoint',
        'request_url',
        'request_payload',
        'http_status',
        'response_payload',
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
