from django.urls import path
from twa.views import ApplicationStatusWebhookView, KYCStatusWebhookView

app_name = 'twa'

urlpatterns = [
    path('webhook/application-status', ApplicationStatusWebhookView.as_view(), name='application_status_webhook'),
    path('webhook/kyc-status', KYCStatusWebhookView.as_view(), name='kyc_status_webhook'),
]
