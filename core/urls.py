
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

    #Import contacts from csv file
    path("contacts/import/", views.import_contacts_csv, name="import_contacts_csv"),
    path("contacts/template/", views.download_contacts_template, name="download_contacts_template"),

    
    path('sheet/<int:source_id>/', views.view_full_sheet, name='view_full_sheet'),

    # ---- RISK PULSE ----
    path('risk/', views.risk_overview, name='risk_overview'),
    path('risk/<int:project_id>/', views.risk_detail, name='risk_detail'),
    path('risk/run-all/', views.risk_run_all, name='risk_run_all'),
    path('risk/<int:project_id>/refresh/', views.risk_refresh_api, name='risk_refresh_api'),

    # Export to Project Daily Logs, observation and Alerts to Procore
    path("projects/<int:project_id>/export-to-procore/", views.export_to_procore, name="export_to_procore"),

    # --- Meetings ---
    path("meetings/", views.meetings_list, name="meetings_list"),
    path("meetings/upload/", views.upload_meeting, name="upload_meeting"),
    path("meetings/<int:pk>/status/", views.meeting_status, name="meeting_status"),
    path("meetings/<int:pk>/status.json", views.meeting_status_json, name="meeting_status_json"),
    path("meetings/<int:pk>/review/", views.meeting_review, name="meeting_review"),
    path("meetings/<int:pk>/approve/", views.meeting_approve, name="meeting_approve"),
    path("action-items/<int:pk>/update/", views.action_item_update, name="action_item_update"),
    path("action-items/<int:pk>/delete/", views.action_item_delete, name="action_item_delete"),

    # --- Export Meeting Items to Procore ---
    path("projects/<int:pk>/procore-setup/",     views.procore_setup,            name="procore_setup"),
    path("projects/<int:pk>/procore-connect/",   views.procore_oauth_redirect,   name="procore_oauth_redirect"),
    path("projects/<int:pk>/procore-test/",      views.procore_test_connection,   name="procore_test_connection"),
    path("procore/callback/",                    views.procore_oauth_callback,    name="procore_oauth_callback"),
    path("meetings/<int:pk>/procore-dry-run/",   views.meeting_procore_dry_run,   name="meeting_procore_dry_run"),
    path("meetings/<int:pk>/procore-push/",      views.meeting_procore_push,      name="meeting_procore_push"),

    path("dev-tools/",        views.dev_tools,        name="dev_tools"),
    path("dev-tools/action/", views.dev_tools_action, name="dev_tools_action"),

   
]

from core.procore_mock_views import (
    mock_procore_me,
    mock_procore_project,
    mock_procore_meeting,
    mock_procore_action_item,
    mock_procore_store,
    mock_procore_daily_log,
    mock_procore_observation,
    mock_procore_image,
)

urlpatterns += [
    path("mock-procore/rest/v1.0/me",
         mock_procore_me,          name="mock_procore_me"),
    path("mock-procore/rest/v1.0/projects/<str:project_id>",
         mock_procore_project,     name="mock_procore_project"),
    path("mock-procore/rest/v1.0/projects/<str:project_id>/meetings",
         mock_procore_meeting,     name="mock_procore_meeting"),
    path("mock-procore/rest/v1.0/projects/<str:project_id>/meetings/<str:meeting_id>/meeting_action_items",
         mock_procore_action_item, name="mock_procore_action_item"),
    path("mock-procore/store/",
         mock_procore_store,       name="mock_procore_store"),
    path("mock-procore/rest/v1.0/projects/<str:project_id>/daily_logs",
         mock_procore_daily_log,   name="mock_procore_daily_log"),
    path("mock-procore/rest/v1.0/projects/<str:project_id>/observations/items",
         mock_procore_observation, name="mock_procore_observation"),
    path("mock-procore/rest/v1.0/projects/<str:project_id>/images",
         mock_procore_image,       name="mock_procore_image"),
]



