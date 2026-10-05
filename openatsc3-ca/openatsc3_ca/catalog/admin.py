"""Django admin for the CA catalog (v1 is admin-only).

Issuing/revoking through the admin calls the ``services`` layer, never the
crypto directly.
"""

from __future__ import annotations

from django.contrib import admin

from . import services
from .models import (
    AuditEvent,
    Broadcaster,
    Certificate,
    OcspResponder,
    OcspResponse,
    PublishedSignaling,
    RolloverPlan,
    SpecReference,
)


@admin.register(Broadcaster)
class BroadcasterAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "bsids", "created_at")
    search_fields = ("name", "organization")


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("common_name", "profile", "status", "serial",
                    "not_after", "broadcaster")
    list_filter = ("profile", "status", "broadcaster")
    search_fields = ("serial", "common_name", "subject_dn", "ski", "aki")
    readonly_fields = ("serial", "ski", "aki", "subject_dn", "der_file",
                       "key_path", "key_sha256", "created_at", "updated_at")
    actions = ("revoke_certificates",)

    @admin.action(description="Revoke selected certificates")
    def revoke_certificates(self, request, queryset):
        count = 0
        for cert in queryset.exclude(status="revoked"):
            services.revoke(cert, reason="admin action",
                            actor=getattr(request.user, "username", "admin"))
            count += 1
        self.message_user(request, f"revoked {count} certificate(s)")


@admin.register(OcspResponder)
class OcspResponderAdmin(admin.ModelAdmin):
    list_display = ("common_name", "active", "certificate", "created_at")
    list_filter = ("active",)


@admin.register(OcspResponse)
class OcspResponseAdmin(admin.ModelAdmin):
    list_display = ("certificate", "status", "produced_at", "next_update",
                    "responder")
    list_filter = ("status", "responder")
    readonly_fields = ("der_file", "sha256", "created_at")


@admin.register(RolloverPlan)
class RolloverPlanAdmin(admin.ModelAdmin):
    list_display = ("broadcaster", "current", "next", "next_from",
                    "current_until", "state")
    list_filter = ("state",)


@admin.register(PublishedSignaling)
class PublishedSignalingAdmin(admin.ModelAdmin):
    list_display = ("broadcaster", "signing_time", "signer", "created_at")


@admin.register(SpecReference)
class SpecReferenceAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "ref")
    search_fields = ("key", "value", "ref")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "target_type", "target_id")
    list_filter = ("action", "actor")
    search_fields = ("action", "actor", "target_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
