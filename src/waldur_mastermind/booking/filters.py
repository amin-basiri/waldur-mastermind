from collections.abc import Iterable

from django.core import exceptions as django_exceptions
from django.db.models import Q
from django_filters import OrderingFilter, UUIDFilter
from rest_framework.filters import BaseFilterBackend

from waldur_core.structure import models as structure_models
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace.filters import ResourceFilter

from . import PLUGIN_NAME


class ResourceOwnerOrCreatorFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        user = request.user
        if user.is_staff:
            return queryset
        else:
            customers = structure_models.CustomerPermission.objects.filter(
                user=user,
                role__in=[
                    structure_models.CustomerRole.OWNER,
                    structure_models.CustomerRole.SERVICE_MANAGER,
                ],
            ).values_list('customer', flat=True)

            try:
                resource_ids = marketplace_models.OrderItem.objects.filter(
                    type=marketplace_models.RequestTypeMixin.Types.CREATE,
                    offering__type=PLUGIN_NAME,
                    order__created_by=user,
                ).values_list('resource_id', flat=True)
            except (
                django_exceptions.ObjectDoesNotExist,
                django_exceptions.MultipleObjectsReturned,
            ):
                resource_ids = []

            return queryset.filter(
                Q(offering__customer_id__in=customers) | Q(id__in=resource_ids)
            )


class CustomersFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        user = request.user
        if user.is_staff:
            return queryset
        else:
            customers = structure_models.CustomerPermission.objects.filter(
                user=user, role=structure_models.CustomerRole.OWNER
            ).values_list('customer', flat=True)
            return queryset.filter(customer_id__in=customers)


class SchedulesOrderingFilter(OrderingFilter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra['choices'] += [
            ('schedules', 'Schedules'),
            ('-schedules', 'Schedules (descending)'),
        ]

    def filter(self, qs, value):
        if isinstance(value, Iterable) and any(
            v in ['schedules', '-schedules'] for v in value
        ):
            # This code works if the first record is the earliest booking.
            # TODO: Add model 'Slot'
            qs = qs.extra(
                select={
                    'schedules': "((marketplace_resource.attributes::json->'schedules'->>0)::json->>'start')"
                }
            )

        return super().filter(qs, value)


class BookingResourceFilter(ResourceFilter):
    o = SchedulesOrderingFilter(fields=('name', 'created', 'type'))
    connected_customer_uuid = UUIDFilter(method='filter_connected_customer')

    def filter_connected_customer(self, queryset, name, value):
        return queryset.filter(
            Q(
                project__customer__uuid=value,
            )
            | Q(
                offering__customer__uuid=value,
            )
        )
