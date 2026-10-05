"""URL routing for the OpenATSC3 CA app (admin only in v1)."""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
