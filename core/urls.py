
from django.urls import path
from . import views

urlpatterns = [
    # Main views
    path('dashboard/', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('', views.home_view, name='home'),
    path('about/', views.about_view, name='about'),
    path('contact/', views.contact_view, name='contact'),
    path('subscribe/', views.subscribe_view, name='subscribe'),
    path('logout/', views.logoutuser, name='logout'),

    
    # Webhooks
    path('webhook/whatsapp/', views.whatsapp_webhook, name='whatsapp_webhook'),
    
    # Project management
    path('switch-project/<int:project_id>/', views.switch_project, name='switch_project'),
    path('manage-contacts/', views.manage_contacts, name='manage_contacts'),
    
    # Data export
    path('export/', views.export_data, name='export_data'),
    
    # Manual sync triggers
    path('sync/', views.trigger_sync, name='trigger_sync'),
    
    # API endpoints for AJAX
    path('api/recent-messages/', views.api_recent_messages, name='api_recent_messages'),
    path('api/alerts/', views.api_alerts, name='api_alerts'),

    #manage projects
    path('manage-projects/', views.manage_projects, name='manage_projects'),

    #manage data sources for each project
    path('manage-data-sources/', views.manage_data_sources, name='manage_data_sources'),
    
    path('sheet/<int:source_id>/', views.view_full_sheet, name='view_full_sheet'),

    # ---- RISK PULSE ----
    path('risk/', views.risk_overview, name='risk_overview'),
    path('risk/<int:project_id>/', views.risk_detail, name='risk_detail'),
    path('risk/run-all/', views.risk_run_all, name='risk_run_all'),
    path('risk/<int:project_id>/refresh/', views.risk_refresh_api, name='risk_refresh_api'),
   
]