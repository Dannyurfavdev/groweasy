from django.contrib import admin

from .models import RiskSnapshot, Project

# Register your models here.

admin.site.register(RiskSnapshot)
admin.site.register(Project)
