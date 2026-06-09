import re
from rest_framework import serializers
from applications.models import Student

class StudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        # Exclude internal/system fields and m2p/twa responses from inbound serializing
        exclude = [
            'tracking_id',
            'pan_number',
            'otp_attempt_count',
            'otp_locked',
            'application_status',
            'kyc_status',
            'm2p_entity_id',
            'm2p_kit_no',
            'm2p_token',
            'twa_synced',
            'pan_verified',
            'pan_name_match_score',
            'created_at',
            'updated_at'
        ]

    def validate_mobile(self, value):
        # Rule: mobile must be exactly 10 digits
        if not re.match(r'^\d{10}$', value):
            raise serializers.ValidationError("Mobile number must be exactly 10 digits, with no country code or spaces.")
        return value

    def validate_current_address(self, value):
        self._validate_pincode_in_address(value, 'current_address')
        return value

    def validate_permanent_address(self, value):
        self._validate_pincode_in_address(value, 'permanent_address')
        return value

    def _validate_pincode_in_address(self, address_dict, field_name):
        if not isinstance(address_dict, dict):
            raise serializers.ValidationError(f"{field_name} must be a valid JSON object.")

        # Find pincode key case-insensitively
        pincode = None
        for key in address_dict.keys():
            if key.lower() in ('pincode', 'pin_code', 'pin', 'postal_code', 'postalcode'):
                pincode = str(address_dict[key]).strip()
                break

        if pincode is None:
            raise serializers.ValidationError(f"{field_name} is missing a pincode/postal_code key.")

        # Rule: pin_code is 5-10 chars
        if len(pincode) < 5 or len(pincode) > 10:
            raise serializers.ValidationError(f"Pincode in {field_name} must be between 5 and 10 characters.")
