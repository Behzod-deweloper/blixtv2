from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class MangalabUserAdmin(UserAdmin):
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {"fields": ("email",)}),
    )
    list_display = ["email", "username", "is_staff", "is_active"]
    ordering = ["email"]
    search_fields = ["email", "username"]