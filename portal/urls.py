from django.urls import path
from portal.views import (
    LandingView, DigiLockerStartView, DigiLockerCallbackView, 
    OTPVerifyView, M2PResendOTPView, SuccessView, LockedView, M2PCryptoTestView
)

app_name = 'portal'

from django.conf import settings

urlpatterns = [
    path('portal/<str:tracking_id>/', LandingView.as_view(), name='landing'),
    path('portal/<str:tracking_id>/aadhaar/digilocker/start/', DigiLockerStartView.as_view(), name='digilocker_start'),
    path('portal/<str:tracking_id>/aadhaar/digilocker/callback/', DigiLockerCallbackView.as_view(), name='digilocker_callback'),
    path('portal/<str:tracking_id>/otp/', OTPVerifyView.as_view(), name='otp_verify'),
    path('portal/<str:tracking_id>/m2p/resend-otp/', M2PResendOTPView.as_view(), name='m2p_resend_otp'),
    path('portal/<str:tracking_id>/success/', SuccessView.as_view(), name='success'),
    path('portal/<str:tracking_id>/locked/', LockedView.as_view(), name='locked'),
]

if settings.DEBUG:
    urlpatterns.insert(0, path('portal/test-m2p-crypto/', M2PCryptoTestView.as_view(), name='test_m2p_crypto'))

