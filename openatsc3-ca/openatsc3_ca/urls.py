"""URL routing for the OpenATSC3 CA app (admin only in v1)."""

from django.contrib import admin
from django.urls import path

from openatsc3_ca.catalog import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("stripe/webhook/", views.stripe_webhook, name="stripe-webhook"),
]
