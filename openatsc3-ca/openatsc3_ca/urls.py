"""URL routing for the OpenATSC3 CA app (admin, Stripe webhook, customer API)."""

from django.contrib import admin
from django.urls import path

from openatsc3_ca.catalog import api, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("stripe/webhook/", views.stripe_webhook, name="stripe-webhook"),
    path("api/v1/tenant/", api.tenant_info, name="api-tenant"),
    path("api/v1/certificates/", api.certificates, name="api-certificates"),
    path("api/v1/smt/", api.upload_smt, name="api-smt"),
]
