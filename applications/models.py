import uuid
from django.db import models

class Student(models.Model):
    GENDER_CHOICES = [
        ('M', 'M'),
        ('F', 'F'),
        ('O', 'O'),
    ]

    APPLICATION_STATUS_CHOICES = [
        ('RECEIVED', 'RECEIVED'),
        ('PROCESSING', 'PROCESSING'),
        ('ISSUED', 'ISSUED'),
        ('REJECTED', 'REJECTED'),
        ('FAILED', 'FAILED'),
    ]

    KYC_STATUS_CHOICES = [
        ('MIN_KYC', 'MIN_KYC'),
        ('FULL_KYC', 'FULL_KYC'),
        ('FAILED', 'FAILED'),
        ('REJECTED', 'REJECTED'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tracking_id = models.CharField(max_length=50, unique=True, db_index=True)
    apaar_id = models.CharField(max_length=50, unique=True, db_index=True)
    title = models.CharField(max_length=10, null=True, blank=True)
    full_name = models.CharField(max_length=200, null=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, null=True, blank=True)
    mobile = models.CharField(max_length=15)
    email = models.EmailField(max_length=200)
    university_name = models.CharField(max_length=200, null=True, blank=True)
    college_name = models.CharField(max_length=200, null=True, blank=True)
    course_name = models.CharField(max_length=200, null=True, blank=True)
    enrollment_number = models.CharField(max_length=100, null=True, blank=True)
    admission_year = models.IntegerField(null=True, blank=True)
    academic_session = models.CharField(max_length=20, null=True, blank=True)
    academic_status = models.CharField(max_length=20, null=True, blank=True)
    blood_group = models.CharField(max_length=5, null=True, blank=True)
    current_address = models.JSONField(null=True, blank=True)
    permanent_address = models.JSONField(null=True, blank=True)
    photo_path = models.CharField(max_length=500, null=True, blank=True)
    aadhaar_number = models.CharField(max_length=20, null=True, blank=True)
    aadhaar_verified = models.BooleanField(default=False)
    aadhaar_name_match_score = models.IntegerField(null=True, blank=True)
    aadhaar_ref_id = models.CharField(max_length=100, null=True, blank=True)
    otp_attempt_count = models.SmallIntegerField(default=0)
    otp_locked = models.BooleanField(default=False)
    application_status = models.CharField(
        max_length=30,
        choices=APPLICATION_STATUS_CHOICES,
        default='RECEIVED'
    )
    kyc_status = models.CharField(
        max_length=30,
        choices=KYC_STATUS_CHOICES,
        default='MIN_KYC'
    )
    m2p_entity_id = models.CharField(max_length=100, null=True, blank=True)
    m2p_kit_no = models.CharField(max_length=100, null=True, blank=True)
    m2p_token = models.TextField(null=True, blank=True)
    twa_synced = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def current_address_line(self):
        if not self.current_address or not isinstance(self.current_address, dict):
            return ""
        return (self.current_address.get('address1') or 
                self.current_address.get('ADDRESS_LINE') or 
                self.current_address.get('address_line') or "")

    @property
    def current_address_city(self):
        if not self.current_address or not isinstance(self.current_address, dict):
            return ""
        return (self.current_address.get('city') or 
                self.current_address.get('CITY') or "")

    @property
    def current_address_state(self):
        if not self.current_address or not isinstance(self.current_address, dict):
            return ""
        return (self.current_address.get('state') or 
                self.current_address.get('STATE') or "")

    @property
    def current_address_pincode(self):
        if not self.current_address or not isinstance(self.current_address, dict):
            return ""
        return (self.current_address.get('pincode') or 
                self.current_address.get('pin_code') or 
                self.current_address.get('PIN_CODE') or "")

    @property
    def permanent_address_line(self):
        if not self.permanent_address or not isinstance(self.permanent_address, dict):
            return ""
        return (self.permanent_address.get('address1') or 
                self.permanent_address.get('ADDRESS_LINE') or 
                self.permanent_address.get('address_line') or "")

    @property
    def permanent_address_city(self):
        if not self.permanent_address or not isinstance(self.permanent_address, dict):
            return ""
        return (self.permanent_address.get('city') or 
                self.permanent_address.get('CITY') or "")

    @property
    def permanent_address_state(self):
        if not self.permanent_address or not isinstance(self.permanent_address, dict):
            return ""
        return (self.permanent_address.get('state') or 
                self.permanent_address.get('STATE') or "")

    @property
    def permanent_address_pincode(self):
        if not self.permanent_address or not isinstance(self.permanent_address, dict):
            return ""
        return (self.permanent_address.get('pincode') or 
                self.permanent_address.get('pin_code') or 
                self.permanent_address.get('PIN_CODE') or "")

    class Meta:
        db_table = 'students'

    def __str__(self):
        return f"{self.full_name or 'Student'} ({self.tracking_id})"


class ABCApiLog(models.Model):
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
        related_name='abc_logs'
    )
    tracking_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    endpoint = models.CharField(max_length=100)
    http_method = models.CharField(max_length=10)
    client_id = models.CharField(max_length=100, null=True, blank=True)
    request_payload = models.JSONField(null=True, blank=True)
    encrypted_request_payload = models.JSONField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    encrypted_response_payload = models.JSONField(null=True, blank=True)
    http_status = models.SmallIntegerField(null=True, blank=True)
    hmac_valid = models.BooleanField(null=True, blank=True)
    success = models.BooleanField()
    error_message = models.TextField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True) # outbound only
    source_ip = models.CharField(max_length=45, null=True, blank=True) # inbound only
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'abc_api_logs'

    def __str__(self):
        return f"{self.direction.upper()} | {self.endpoint} | Status: {self.http_status}"


class AadhaarApiLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aadhaar_logs'
    )
    apaar_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    endpoint = models.CharField(max_length=50)
    request_url = models.CharField(max_length=500)
    request_headers = models.JSONField(null=True, blank=True)
    request_payload = models.JSONField(null=True, blank=True)
    http_status = models.SmallIntegerField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    success = models.BooleanField()
    error_message = models.TextField(null=True, blank=True)
    duration_ms = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'aadhaar_api_logs'

    def __str__(self):
        return f"Aadhaar Verify | {self.apaar_id} | Status: {self.http_status}"
