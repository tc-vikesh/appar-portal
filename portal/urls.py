from django.urls import path
from portal.views import LandingView, OTPVerifyView, SuccessView

app_name = 'portal'

urlpatterns = [
    path('portal/<str:tracking_id>/', LandingView.as_view(), name='landing'),
    path('portal/<str:tracking_id>/otp/', OTPVerifyView.as_view(), name='otp_verify'),
    path('portal/<str:tracking_id>/success/', SuccessView.as_view(), name='success'),
]
