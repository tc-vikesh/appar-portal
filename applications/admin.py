from django.contrib import admin
from applications.models import Student, ABCApiLog, AadhaarApiLog

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        'tracking_id',
        'apaar_id',
        'full_name',
        'mobile',
        'aadhaar_verified',
        'application_status',
        'kyc_status',
        'twa_synced',
        'created_at'
    )
    list_filter = (
        'application_status',
        'kyc_status',
        'aadhaar_verified',
        'twa_synced',
        'admission_year',
        'gender'
    )
    search_fields = (
        'tracking_id',
        'apaar_id',
        'full_name',
        'mobile',
        'email',
        'university_name'
    )
    readonly_fields = (
        'id',
        'tracking_id',
        'apaar_id',
        'created_at',
        'updated_at'
    )
    fieldsets = (
        ('System Identifiers', {
            'fields': ('id', 'tracking_id', 'apaar_id')
        }),
        ('Personal Details', {
            'fields': ('full_name', 'dob', 'gender', 'mobile', 'email', 'blood_group', 'aadhaar_number', 'aadhaar_verified', 'aadhaar_name_match_score', 'aadhaar_ref_id')
        }),
        ('Academic Details', {
            'fields': (
                'university_name',
                'college_name',
                'course_name',
                'enrollment_number',
                'admission_year',
                'academic_session',
                'academic_status'
            )
        }),
        ('Address Information', {
            'fields': ('current_address', 'permanent_address')
        }),
        ('KYC & Integration Keys (M2P / TWA)', {
            'fields': (
                'm2p_entity_id',
                'm2p_kit_no',
                'm2p_token',
                'twa_synced',
                'otp_attempt_count',
                'otp_locked'
            )
        }),
        ('App Status', {
            'fields': ('application_status', 'kyc_status')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        })
    )


@admin.register(ABCApiLog)
class ABCApiLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'student',
        'tracking_id',
        'direction',
        'endpoint',
        'http_status',
        'success',
        'created_at'
    )
    list_filter = ('direction', 'success', 'http_status', 'hmac_valid')
    search_fields = ('tracking_id', 'endpoint', 'client_id', 'source_ip')
    readonly_fields = (
        'id',
        'student',
        'tracking_id',
        'direction',
        'endpoint',
        'http_method',
        'client_id',
        'request_payload',
        'response_payload',
        'http_status',
        'hmac_valid',
        'success',
        'error_message',
        'duration_ms',
        'source_ip',
        'created_at'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AadhaarApiLog)
class AadhaarApiLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'student',
        'apaar_id',
        'endpoint',
        'http_status',
        'success',
        'duration_ms',
        'created_at'
    )
    list_filter = ('success', 'http_status')
    search_fields = ('apaar_id', 'endpoint')
    readonly_fields = (
        'id',
        'student',
        'apaar_id',
        'endpoint',
        'request_url',
        'request_headers',
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
