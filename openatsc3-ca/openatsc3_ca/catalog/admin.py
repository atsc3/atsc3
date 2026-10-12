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
    Order,
    PublishedSignaling,
    RolloverPlan,
    SpecReference,
    Tenant,
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "organization", "active", "created_at")
    search_fields = ("name", "slug", "organization")
    list_filter = ("active",)


@admin.register(Broadcaster)
class BroadcasterAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "tenant", "bsids", "created_at")
    search_fields = ("name", "organization")
    list_filter = ("tenant",)


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("common_name", "profile", "status", "serial",
                    "not_after", "tenant", "broadcaster")
    list_filter = ("profile", "status", "tenant", "broadcaster")
    search_fields = ("serial", "common_name", "subject_dn", "ski", "aki")
    readonly_fields = ("serial", "ski", "aki", "subject_dn", "der_file",
                       "key_path", "key_sha256", "kms_key_id", "created_at",
                       "updated_at")
    actions = ("revoke_certificates",)

    @admin.action(description="Revoke selected certificates")
    def revoke_certificates(self, request, queryset):
        count = 0
        for cert in queryset.exclude(status="revoked"):
            services.revoke(cert, reason="admin action",
                            actor=getattr(request.user, "username", "admin"))
            count += 1
        self.message_user(request, f"revoked {count} certificate(s)")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "organization", "status", "amount_cents",
                    "tenant", "fulfilled_at", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("email", "organization", "stripe_session_id",
                     "stripe_payment_intent")
    readonly_fields = ("stripe_session_id", "stripe_payment_intent", "checkout_url",
                       "delivery_url", "paid_at", "fulfilled_at", "refunded_at",
                       "tenant", "broadcaster", "created_at", "updated_at")
    actions = ("approve_orders", "refund_orders")

    @admin.action(description="Approve and fulfill selected paid orders")
    def approve_orders(self, request, queryset):
        actor = getattr(request.user, "username", "admin")
        count = 0
        for order in queryset.filter(status="paid"):
            services.fulfill_order(order, actor=actor)
            count += 1
        self.message_user(request, f"fulfilled {count} order(s)")

    @admin.action(description="Refund selected orders (revokes the tenant CA)")
    def refund_orders(self, request, queryset):
        actor = getattr(request.user, "username", "admin")
        count = 0
        for order in queryset.exclude(status="refunded"):
            services.refund_order(order, actor=actor)
            count += 1
        self.message_user(request, f"refunded {count} order(s)")


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
