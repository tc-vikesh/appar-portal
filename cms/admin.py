from django.contrib import admin
from cms.models import CMSPage

@admin.register(CMSPage)
class CMSPageAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'is_active', 'updated_at', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('title', 'slug', 'content')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('created_at', 'updated_at')
