from django.contrib import admin
from .models import Company, UserProfile, SubscriptionPlan, CashFlowEntry, UploadHistory

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['name', 'tax_id', 'phone', 'created_at', 'is_active']
    search_fields = ['name', 'tax_id']
    list_filter = ['is_active', 'created_at']

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'company', 'role']
    search_fields = ['user__username', 'company__name']

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ['company', 'plan_type', 'price_mzn', 'start_date', 'end_date']
    list_filter = ['plan_type', 'is_active']

@admin.register(CashFlowEntry)
class CashFlowEntryAdmin(admin.ModelAdmin):
    list_display = ['description', 'amount', 'entry_type', 'transaction_date', 'company', 'source']
    list_filter = ['entry_type', 'source', 'transaction_date']
    search_fields = ['description', 'company__name']
    date_hierarchy = 'transaction_date'

@admin.register(UploadHistory)
class UploadHistoryAdmin(admin.ModelAdmin):
    list_display = ['filename', 'company', 'rows_processed', 'uploaded_at', 'status']
    list_filter = ['status', 'uploaded_at']
    search_fields = ['filename', 'company__name']