from django.urls import path
from . import views

app_name = 'financas'

urlpatterns = [
    # Dashboards
    path('dashboard/', views.dashboard, name='dashboard'),
    
    # Dados via HTMX
    path('api/indicators/', views.get_indicators, name='get_indicators'),
    path('api/transactions/', views.get_recent_transactions, name='get_recent_transactions'),
    path('api/chart-data/', views.get_chart_data, name='get_chart_data'),
    path('api/upload-history/', views.get_upload_history, name='get_upload_history'),
    path('api/category-chart/', views.get_category_chart_data, name='get_category_chart_data'),
    path('api/monthly-comparison/', views.get_monthly_comparison, name='get_monthly_comparison'),
    
    # Upload
    path('upload/', views.upload_page, name='upload'),
    path('process-upload/', views.process_upload, name='process_upload'),
    
    # Entrada Manual
    path('add-transaction/', views.add_transaction, name='add_transaction'),
    path('get-add-form/', views.get_add_form, name='get_add_form'),
    
    # Edição e Exclusão
    path('edit/<int:transaction_id>/', views.edit_transaction, name='edit_transaction'),
    path('delete/<int:transaction_id>/', views.delete_transaction, name='delete_transaction'),
    path('get-edit-form/<int:transaction_id>/', views.get_edit_form, name='get_edit_form'),
    
    # Limpar Histórico
    path('clear-upload-history/', views.clear_upload_history, name='clear_upload_history'),
    
    # Relatórios
    path('relatorio-pdf/', views.generate_report_pdf, name='generate_report_pdf'),
    path('relatorio-graficos-pdf/', views.generate_report_with_charts, name='generate_report_with_charts'),
    path('relatorio-categoria-pdf/', views.generate_report_with_charts, name='generate_report_with_charts'),
    path('previsao-fluxo-pdf/', views.generate_forecast_report, name='generate_forecast_report'),
    path('metas-performance-pdf/', views.generate_goals_report, name='generate_goals_report'),
    # Exportar Excel
    path('export-excel/', views.export_excel, name='export_excel'),
    
    # Metas
    path('goals/', views.goals_page, name='goals_page'),
    path('add-goal/', views.add_goal, name='add_goal'),
    path('get-goals-form/', views.get_goals_form, name='get_goals_form'),
    path('delete-goal/<int:goal_id>/', views.delete_goal, name='delete_goal'),
    
    # Redirecionamento
    path('', views.dashboard, name='index'),
]