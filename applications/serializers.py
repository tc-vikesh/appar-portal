import re
from datetime import datetime
import dateutil.parser
from rest_framework import serializers
from applications.models import Student

def normalize_dob(dob_val):
    if not dob_val:
        return None
    dob_str = str(dob_val).strip()
    if not dob_str:
        return None

    # Check common explicit date formats
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y'):
        try:
            return datetime.strptime(dob_str, fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue

    # Fallback to dateutil with dayfirst=True
    try:
        dt = dateutil.parser.parse(dob_str, dayfirst=True)
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return dob_str


def normalize_gender(gender_val):
    if not gender_val:
        return None
    g = str(gender_val).strip().upper()
    if g in ('M', 'MALE', 'BOY', 'MAN'):
        return 'M'
    elif g in ('F', 'FEMALE', 'GIRL', 'WOMAN'):
        return 'F'
    elif g in ('O', 'OTHER', 'TRANSGENDER', 'TG'):
        return 'O'
    if g and g[0] in ('M', 'F', 'O'):
        return g[0]
    return gender_val


class StudentSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = data.copy()
            if 'dob' in data and data['dob']:
                data['dob'] = normalize_dob(data['dob'])
            if 'gender' in data and data['gender']:
                norm_gender = normalize_gender(data['gender'])
                data['gender'] = norm_gender
                if not data.get('title') and norm_gender:
                    if norm_gender == 'M':
                        data['title'] = 'Mr'
                    elif norm_gender == 'F':
                        data['title'] = 'Ms'
                    else:
                        data['title'] = 'Mx'
        return super().to_internal_value(data)

    class Meta:
        model = Student
        # Exclude internal/system fields and m2p/twa responses from inbound serializing
        exclude = [
            'tracking_id',
            'aadhaar_number',
            'otp_attempt_count',
            'otp_locked',
            'application_status',
            'kyc_status',
            'm2p_entity_id',
            'm2p_kit_no',
            'm2p_token',
            'twa_synced',
            'aadhaar_verified',
            'aadhaar_name_match_score',
            'aadhaar_ref_id',
            'created_at',
            'updated_at'
        ]

    def validate_mobile(self, value):
        # Rule: mobile must be exactly 10 digits
        if not re.match(r'^\d{10}$', value):
            raise serializers.ValidationError("Mobile number must be exactly 10 digits, with no country code or spaces.")
        return value

    def validate_current_address(self, value):
        if value is None:
            return value
        self._validate_pincode_in_address(value, 'current_address')
        return value

    def validate_permanent_address(self, value):
        if value is None:
            return value
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
