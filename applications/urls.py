from django.urls import path
from applications.views import (
    ReceiveApplicationView,
    AcknowledgeApplicationView,
    ApplicationStatusView,
    DashboardStatsView
)

app_name = 'applications'

urlpatterns = [
    path('application/receive', ReceiveApplicationView.as_view(), name='receive_application'),
    path('application/acknowledge', AcknowledgeApplicationView.as_view(), name='acknowledge_application'),
    path('application/status/<str:tracking_id>', ApplicationStatusView.as_view(), name='application_status'),
    path('dashboard/stats', DashboardStatsView.as_view(), name='dashboard_stats'),
]
