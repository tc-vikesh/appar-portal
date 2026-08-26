from django.urls import path
from portal.views import LandingView, AadhaarSendOTPView, AadhaarVerifyOTPView, OTPVerifyView, SuccessView, LockedView, M2PCryptoTestView

app_name = 'portal'

urlpatterns = [
    path('portal/test-m2p-crypto/', M2PCryptoTestView.as_view(), name='test_m2p_crypto'),
    path('portal/<str:tracking_id>/', LandingView.as_view(), name='landing'),
    path('portal/<str:tracking_id>/aadhaar/send-otp/', AadhaarSendOTPView.as_view(), name='aadhaar_send_otp'),
    path('portal/<str:tracking_id>/aadhaar/verify-otp/', AadhaarVerifyOTPView.as_view(), name='aadhaar_verify_otp'),
    path('portal/<str:tracking_id>/otp/', OTPVerifyView.as_view(), name='otp_verify'),
    path('portal/<str:tracking_id>/success/', SuccessView.as_view(), name='success'),
    path('portal/<str:tracking_id>/locked/', LockedView.as_view(), name='locked'),
]

