import copy
import datetime
import logging
import textwrap

import reversion
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    OuterRef,
    PositiveSmallIntegerField,
    Q,
    Subquery,
)
from django.db.models.aggregates import Sum
from django.db.models.fields import FloatField, IntegerField
from django.db.models.functions import Coalesce
from django.db.models.functions.math import Ceil
from django.http.response import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django_filters.rest_framework import DjangoFilterBackend
from django_fsm import TransitionNotAllowed
from rest_framework import exceptions as rf_exceptions
from rest_framework import mixins
from rest_framework import permissions as rf_permissions
from rest_framework import status, views
from rest_framework import viewsets as rf_viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from waldur_client import WaldurClientException

from waldur_core.core import models as core_models
from waldur_core.core import permissions as core_permissions
from waldur_core.core import utils as core_utils
from waldur_core.core import validators as core_validators
from waldur_core.core import views as core_views
from waldur_core.core.mixins import EagerLoadMixin
from waldur_core.core.renderers import PlainTextRenderer
from waldur_core.core.utils import is_uuid_like, month_start, order_with_nulls
from waldur_core.structure import filters as structure_filters
from waldur_core.structure import models as structure_models
from waldur_core.structure import permissions as structure_permissions
from waldur_core.structure import serializers as structure_serializers
from waldur_core.structure import utils as structure_utils
from waldur_core.structure import views as structure_views
from waldur_core.structure.exceptions import ServiceBackendError
from waldur_core.structure.managers import filter_queryset_for_user
from waldur_core.structure.permissions import _has_owner_access
from waldur_core.structure.registry import get_resource_type
from waldur_core.structure.serializers import (
    ProjectUserSerializer,
    get_resource_serializer_class,
)
from waldur_core.structure.signals import resource_imported
from waldur_mastermind.invoices import models as invoice_models
from waldur_mastermind.invoices import serializers as invoice_serializers
from waldur_mastermind.marketplace import callbacks
from waldur_mastermind.marketplace.utils import validate_attributes
from waldur_mastermind.marketplace_slurm_remote import (
    PLUGIN_NAME as SLURM_REMOTE_PLUGIN_NAME,
)
from waldur_mastermind.promotions import models as promotions_models
from waldur_mastermind.support import models as support_models
from waldur_pid import models as pid_models

from . import filters, log, models, permissions, plugins, serializers, tasks, utils

logger = logging.getLogger(__name__)


class BaseMarketplaceView(core_views.ActionsViewSet):
    lookup_field = 'uuid'
    filter_backends = (DjangoFilterBackend,)
    update_permissions = partial_update_permissions = destroy_permissions = [
        structure_permissions.is_owner
    ]


class PublicViewsetMixin:
    def get_permissions(self):
        if settings.WALDUR_MARKETPLACE[
            'ANONYMOUS_USER_CAN_VIEW_OFFERINGS'
        ] and self.action in ['list', 'retrieve']:
            return [rf_permissions.AllowAny()]
        else:
            return super().get_permissions()


class ConnectedOfferingDetailsMixin:
    @action(detail=True, methods=['get'])
    def offering(self, request, *args, **kwargs):
        requested_object = self.get_object()
        if hasattr(requested_object, 'offering'):
            offering = requested_object.offering
            serializer = serializers.PublicOfferingDetailsSerializer(
                instance=offering, context=self.get_serializer_context()
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            return Response(status.HTTP_204_NO_CONTENT)


class ServiceProviderViewSet(PublicViewsetMixin, BaseMarketplaceView):
    queryset = models.ServiceProvider.objects.all().order_by('customer__name')
    serializer_class = serializers.ServiceProviderSerializer
    filterset_class = filters.ServiceProviderFilter
    api_secret_code_permissions = (
        projects_permissions
    ) = (
        project_permissions_permissions
    ) = keys_permissions = users_permissions = set_offerings_username_permissions = [
        structure_permissions.is_owner
    ]

    @action(detail=True, methods=['GET', 'POST'])
    def api_secret_code(self, request, uuid=None):
        """On GET request - return service provider api_secret_code.
        On POST - generate new service provider api_secret_code.
        """
        service_provider = self.get_object()
        if request.method == 'GET':
            return Response(
                {'api_secret_code': service_provider.api_secret_code},
                status=status.HTTP_200_OK,
            )
        else:
            service_provider.generate_api_secret_code()
            service_provider.save()
            return Response(
                {
                    'detail': _('Api secret code updated.'),
                    'api_secret_code': service_provider.api_secret_code,
                },
                status=status.HTTP_200_OK,
            )

    def get_customer_project_ids(self):
        service_provider = self.get_object()
        return utils.get_service_provider_project_ids(service_provider)

    def get_customer_user_ids(self):
        service_provider = self.get_object()
        return utils.get_service_provider_user_ids(self.request.user, service_provider)

    @action(detail=True, methods=['GET'])
    def customers(self, request, uuid=None):
        service_provider = self.get_object()
        customer_ids = utils.get_service_provider_customer_ids(service_provider)
        customers = structure_models.Customer.objects.filter(id__in=customer_ids)
        page = self.paginate_queryset(customers)
        serializer = serializers.ProviderCustomerSerializer(
            page,
            many=True,
            context={
                'service_provider': service_provider,
                **self.get_serializer_context(),
            },
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def customer_projects(self, request, uuid=None):
        service_provider = self.get_object()
        customer_uuid = request.query_params.get('project_customer_uuid')
        if not customer_uuid or not is_uuid_like(customer_uuid):
            return self.get_paginated_response([])
        project_ids = (
            utils.get_service_provider_resources(service_provider)
            .filter(project__customer__uuid=customer_uuid)
            .values_list('project_id', flat=True)
        )
        projects = structure_models.Project.available_objects.filter(id__in=project_ids)
        page = self.paginate_queryset(projects)
        context = self.get_serializer_context()
        context['service_provider'] = service_provider
        serializer = serializers.ProviderCustomerProjectSerializer(
            page, many=True, context=context
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def projects(self, request, uuid=None):
        project_ids = self.get_customer_project_ids()
        projects = structure_models.Project.available_objects.filter(id__in=project_ids)
        page = self.paginate_queryset(projects)
        serializer = structure_serializers.ProjectSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def project_permissions(self, request, uuid=None):
        project_ids = self.get_customer_project_ids()
        permissions = structure_models.ProjectPermission.objects.filter(
            project_id__in=project_ids, is_active=True
        )
        page = self.paginate_queryset(permissions)
        serializer = structure_serializers.ProjectPermissionLogSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def keys(self, request, uuid=None):
        user_ids = self.get_customer_user_ids()
        keys = core_models.SshPublicKey.objects.filter(user_id__in=user_ids)
        page = self.paginate_queryset(keys)
        serializer = structure_serializers.SshKeySerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def users(self, request, uuid=None):
        service_provider = self.get_object()
        user_ids = self.get_customer_user_ids()
        users = core_models.User.objects.filter(id__in=user_ids)
        page = self.paginate_queryset(users)
        context = self.get_serializer_context()
        context['service_provider'] = service_provider
        serializer = serializers.DetailedProviderUserSerializer(
            page, many=True, context=context
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def user_customers(self, request, uuid=None):
        service_provider = self.get_object()
        user_uuid = request.query_params.get('user_uuid')
        if not user_uuid or not is_uuid_like(user_uuid):
            return self.get_paginated_response([])
        resources = utils.get_service_provider_resources(service_provider)
        project_permissions = structure_models.ProjectPermission.objects.filter(
            user__uuid=user_uuid,
            is_active=True,
            project__in=resources.values_list('project_id', flat=True),
        )
        customer_permissions = structure_models.CustomerPermission.objects.filter(
            user__uuid=user_uuid,
            is_active=True,
            customer__in=resources.values_list('project__customer_id', flat=True),
        )
        customers = structure_models.Customer.objects.filter(
            Q(id__in=project_permissions.values_list('project__customer_id'))
            | Q(id__in=customer_permissions.values_list('customer_id'))
        )
        page = self.paginate_queryset(customers)
        context = self.get_serializer_context()
        context['service_provider'] = service_provider
        serializer = serializers.ProviderCustomerSerializer(
            page, many=True, context=context
        )
        return self.get_paginated_response(serializer.data)

    def check_related_resources(request, view, obj=None):
        if obj and obj.has_active_offerings:
            raise rf_exceptions.ValidationError(
                _('Service provider has active offerings. Please archive them first.')
            )

    destroy_permissions = [structure_permissions.is_owner, check_related_resources]

    @action(detail=True, methods=['POST'])
    def set_offerings_username(self, request, uuid=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_uuid = serializer.validated_data['user_uuid']
        username = serializer.validated_data['username']

        try:
            user = core_models.User.objects.get(uuid=user_uuid)
        except core_models.User.DoesNotExist:
            validation_message = f'A user with the uuid [{user_uuid}] is not found.'
            raise rf_exceptions.ValidationError(_(validation_message))

        user_projects_ids = structure_models.ProjectPermission.objects.filter(
            user=user,
            is_active=True,
        ).values_list('project_id', flat=True)
        offering_ids = (
            models.Resource.objects.exclude(state=models.Resource.States.TERMINATED)
            .filter(
                project_id__in=user_projects_ids,
                offering__customer=self.get_object().customer,
            )
            .values_list('offering_id', flat=True)
        )

        for offering_id in offering_ids:
            models.OfferingUser.objects.update_or_create(
                user=user, offering_id=offering_id, defaults={'username': username}
            )

        return Response(
            {
                'detail': _('Offering users have been set.'),
            },
            status=status.HTTP_201_CREATED,
        )

    set_offerings_username_serializer_class = serializers.SetOfferingsUsernameSerializer

    @action(detail=True, methods=['GET'])
    def offerings(self, request, uuid=None):
        service_provider = self.get_object()

        offerings = models.Offering.objects.filter(
            customer=service_provider.customer,
            billable=True,
            shared=True,
        )

        filtered_offerings = filters.OfferingFilter(request.GET, queryset=offerings)
        page = self.paginate_queryset(filtered_offerings.qs)
        serializer = serializers.ProviderOfferingSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=['GET'])
    def stat(self, request, uuid=None):
        to_day = timezone.datetime.today().date()
        service_provider = self.get_object()

        active_campaigns = promotions_models.Campaign.objects.filter(
            service_provider=service_provider,
            state=promotions_models.Campaign.States.ACTIVE,
            start_date__lte=to_day,
            end_date__gte=to_day,
        ).count()

        current_customers = (
            models.Resource.objects.filter(
                offering__customer=service_provider.customer,
            )
            .exclude(state=models.Resource.States.TERMINATED)
            .order_by()
            .values_list('project__customer', flat=True)
            .distinct()
            .count()
        )

        active_resources = models.Resource.objects.filter(
            offering__customer=service_provider.customer,
        ).exclude(state=models.Resource.States.TERMINATED)

        active_and_paused_offerings = models.Offering.objects.filter(
            customer=service_provider.customer,
            billable=True,
            shared=True,
            state__in=(models.Offering.States.ACTIVE, models.Offering.States.PAUSED),
        ).count()

        content_type = ContentType.objects.get_for_model(support_models.Issue)
        unresolved_tickets = len(
            [
                i
                for i in support_models.Issue.objects.filter(
                    resource_content_type=content_type,
                    resource_object_id__in=(
                        active_resources.values_list('id', flat=True)
                    ),
                )
                if not i.resolved
            ]
        )

        pended_orders = (
            models.OrderItem.objects.filter(
                offering__customer=service_provider.customer,
                order__state=models.Order.States.REQUESTED_FOR_APPROVAL,
            )
            .order_by()
            .values_list('order', flat=True)
            .distinct()
            .count()
        )

        erred_resources = models.Resource.objects.filter(
            offering__customer=service_provider.customer,
            state=models.Resource.States.ERRED,
        ).count()

        return Response(
            {
                'active_campaigns': active_campaigns,
                'current_customers': current_customers,
                'customers_number_change': utils.count_customers_number_change(
                    service_provider
                ),
                'active_resources': active_resources.count(),
                'resources_number_change': utils.count_resources_number_change(
                    service_provider
                ),
                'active_and_paused_offerings': active_and_paused_offerings,
                'unresolved_tickets': unresolved_tickets,
                'pended_orders': pended_orders,
                'erred_resources': erred_resources,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['GET'])
    def revenue(self, request, uuid=None):
        start = month_start(timezone.datetime.today()) - relativedelta(years=1)
        service_provider = self.get_object()
        customer = service_provider.customer

        data = (
            invoice_models.InvoiceItem.objects.filter(
                invoice__year__gte=start.year,
                invoice__month__gte=start.month,
                resource__offering__customer=customer,
            )
            .values('invoice__year', 'invoice__month')
            .annotate(total=Sum(F('unit_price') * F('quantity')))
            .order_by('invoice__year', 'invoice__month')
        )

        return Response(
            serializers.ServiceProviderRevenues(data, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['GET'])
    def robot_account_customers(self, request, uuid=None):
        service_provider = self.get_object()
        qs = models.RobotAccount.objects.filter(
            resource__offering__customer=service_provider.customer
        )
        customer_name = request.query_params.get('customer_name')
        if customer_name:
            qs = qs.filter(resource__project__customer__name__icontains=customer_name)
        customer_ids = qs.values_list('resource__project__customer_id').distinct()
        customers = structure_models.Customer.objects.filter(
            id__in=customer_ids
        ).order_by('name')
        page = self.paginate_queryset(customers)
        data = [{'name': row.name, 'uuid': row.uuid} for row in page]
        return self.get_paginated_response(data)

    @action(detail=True, methods=['GET'])
    def robot_account_projects(self, request, uuid=None):
        service_provider = self.get_object()
        qs = models.RobotAccount.objects.filter(
            resource__offering__customer=service_provider.customer
        )
        project_name = request.query_params.get('project_name')
        if project_name:
            qs = qs.filter(resource__offering__project__name__icontains=project_name)
        project_ids = qs.values_list('resource__project_id').distinct()
        projects = structure_models.Project.objects.filter(id__in=project_ids).order_by(
            'name'
        )
        page = self.paginate_queryset(projects)
        data = [{'name': row.name, 'uuid': row.uuid} for row in page]
        return self.get_paginated_response(data)


class CategoryViewSet(PublicViewsetMixin, EagerLoadMixin, core_views.ActionsViewSet):
    queryset = models.Category.objects.all()
    serializer_class = serializers.CategorySerializer
    lookup_field = 'uuid'
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.CategoryFilter

    create_permissions = (
        update_permissions
    ) = partial_update_permissions = destroy_permissions = [
        structure_permissions.is_staff
    ]


def can_update_offering(request, view, obj=None):
    offering = obj

    if not offering:
        return

    if offering.state == models.Offering.States.DRAFT:
        if offering.has_user(request.user) or _has_owner_access(
            request.user, offering.customer
        ):
            return
        else:
            raise rf_exceptions.PermissionDenied()
    else:
        structure_permissions.is_staff(request, view)


def validate_offering_update(offering):
    if offering.state == models.Offering.States.ARCHIVED:
        raise rf_exceptions.ValidationError(
            _('It is not possible to update archived offering.')
        )


class ProviderOfferingViewSet(
    core_views.CreateReversionMixin,
    core_views.UpdateReversionMixin,
    BaseMarketplaceView,
):
    """
    This viewset enables uniform implementation of resource import.

    Consider the following example:

    importable_resources_backend_method = 'get_tenants_for_import'
    import_resource_executor = executors.TenantImportExecutor

    It is expected that importable_resources_backend_method returns list of dicts, each of which
    contains two mandatory fields: name and backend_id, and one optional field called extra.
    This optional field should be list of dicts, each of which contains two mandatory fields: name and value.

    Note that there are only 3 mandatory parameters:
    * importable_resources_backend_method
    * importable_resources_serializer_class
    * import_resource_serializer_class
    """

    queryset = models.Offering.objects.all()
    serializer_class = serializers.ProviderOfferingDetailsSerializer
    create_serializer_class = serializers.OfferingCreateSerializer
    update_serializer_class = (
        partial_update_serializer_class
    ) = serializers.OfferingUpdateSerializer
    filterset_class = filters.OfferingFilter
    filter_backends = (
        DjangoFilterBackend,
        filters.OfferingCustomersFilterBackend,
        filters.OfferingImportableFilterBackend,
        filters.ExternalOfferingFilterBackend,
    )

    def _check_extra_field_needed(self, field_name):
        return (
            field_name == self.request.query_params.get('o', '')
            or '-' + field_name == self.request.query_params.get('o', '')
            or self.detail
        )

    def get_queryset(self):
        queryset = super().get_queryset()

        # add total_customers
        if self._check_extra_field_needed('total_customers'):
            resources = (
                models.Resource.objects.filter(
                    offering=OuterRef('pk'),
                    state__in=(
                        models.Resource.States.OK,
                        models.Resource.States.UPDATING,
                        models.Resource.States.TERMINATING,
                    ),
                )
                .order_by()
                .values('offering')
            )
            total_customers = resources.annotate(
                total=Count(
                    'project__customer_id',
                    distinct=True,
                    output_field=IntegerField(),
                )
            ).values('total')
            queryset = queryset.annotate(total_customers=Coalesce(total_customers, 0))

        # add total_cost
        if self._check_extra_field_needed('total_cost'):
            items = (
                invoice_models.InvoiceItem.objects.filter(
                    resource__offering=OuterRef('pk'),
                    invoice__year=core_utils.get_last_month().year,
                    invoice__month=core_utils.get_last_month().month,
                )
                .order_by()
                .annotate(
                    price=ExpressionWrapper(
                        F('quantity') * F('unit_price'), output_field=IntegerField()
                    )
                )
                .values('resource__offering')
            )
            total_cost = items.annotate(
                total=Sum(
                    'price',
                    output_field=IntegerField(),
                )
            ).values('total')
            queryset = queryset.annotate(total_cost=Coalesce(total_cost, 0))

        # add total_cost_estimated
        if self._check_extra_field_needed('total_cost_estimated'):
            current_month = datetime.date.today()
            items = (
                invoice_models.InvoiceItem.objects.filter(
                    resource__offering=OuterRef('pk'),
                    invoice__year=current_month.year,
                    invoice__month=current_month.month,
                )
                .order_by()
                .annotate(
                    price=ExpressionWrapper(
                        F('quantity') * F('unit_price'), output_field=IntegerField()
                    )
                )
                .values('resource__offering')
            )
            total_cost = items.annotate(
                total=Sum(
                    'price',
                    output_field=IntegerField(),
                )
            ).values('total')
            queryset = queryset.annotate(total_cost_estimated=Coalesce(total_cost, 0))

        return queryset

    @action(detail=True, methods=['post'])
    def activate(self, request, uuid=None):
        return self._update_state('activate')

    @action(detail=True, methods=['post'])
    def draft(self, request, uuid=None):
        return self._update_state('draft')

    @action(detail=True, methods=['post'])
    def pause(self, request, uuid=None):
        return self._update_state('pause', request)

    pause_serializer_class = serializers.OfferingPauseSerializer

    @action(detail=True, methods=['post'])
    def unpause(self, request, uuid=None):
        return self._update_state('unpause', request)

    @action(detail=True, methods=['post'])
    def archive(self, request, uuid=None):
        return self._update_state('archive')

    def _update_state(self, action, request=None):
        offering = self.get_object()

        try:
            getattr(offering, action)()
        except TransitionNotAllowed:
            raise rf_exceptions.ValidationError(_('Offering state is invalid.'))

        with reversion.create_revision():
            if request:
                serializer = self.get_serializer(
                    offering, data=request.data, partial=True
                )
                serializer.is_valid(raise_exception=True)
                offering = serializer.save()

            offering.save(update_fields=['state'])
            reversion.set_user(self.request.user)
            reversion.set_comment(
                f'Offering state has been updated using method {action}'
            )
        return Response(
            {
                'detail': _('Offering state updated.'),
                'state': offering.get_state_display(),
            },
            status=status.HTTP_200_OK,
        )

    pause_permissions = unpause_permissions = archive_permissions = [
        permissions.user_is_owner_or_service_manager,
    ]

    activate_permissions = [structure_permissions.is_staff]

    activate_validators = pause_validators = archive_validators = destroy_validators = [
        structure_utils.check_customer_blocked_or_archived
    ]

    update_permissions = partial_update_permissions = [can_update_offering]

    update_validators = partial_update_validators = [
        validate_offering_update,
        structure_utils.check_customer_blocked_or_archived,
    ]

    def perform_create(self, serializer):
        customer = serializer.validated_data['customer']
        structure_utils.check_customer_blocked_or_archived(customer)

        super().perform_create(serializer)

    @action(detail=True, methods=['get'])
    def importable_resources(self, request, uuid=None):
        offering = self.get_object()
        method = plugins.manager.get_importable_resources_backend_method(offering.type)
        if (
            not method
            or not offering.scope
            or not hasattr(offering.scope, 'get_backend')
        ):
            raise rf_exceptions.ValidationError(
                'Current offering plugin does not support resource import'
            )

        backend = offering.scope.get_backend()
        resources = getattr(backend, method)()
        page = self.paginate_queryset(resources)
        return self.get_paginated_response(page)

    importable_resources_permissions = [permissions.user_can_list_importable_resources]

    import_resource_permissions = [permissions.user_can_list_importable_resources]

    import_resource_serializer_class = serializers.ImportResourceSerializer

    @action(detail=True, methods=['post'])
    def import_resource(self, request, uuid=None):
        import_resource_serializer = self.get_serializer(data=request.data)
        import_resource_serializer.is_valid(raise_exception=True)

        plan = import_resource_serializer.validated_data.get('plan', None)
        project = import_resource_serializer.validated_data['project']
        backend_id = import_resource_serializer.validated_data['backend_id']

        offering = self.get_object()
        backend = offering.scope.get_backend()
        method = plugins.manager.import_resource_backend_method(offering.type)
        if not method:
            raise rf_exceptions.ValidationError(
                'Current offering plugin does not support resource import'
            )

        resource_model = plugins.manager.get_resource_model(offering.type)

        if resource_model.objects.filter(
            service_settings=offering.scope, backend_id=backend_id
        ).exists():
            raise rf_exceptions.ValidationError(
                _('Resource has been imported already.')
            )

        try:
            resource = getattr(backend, method)(backend_id=backend_id, project=project)
        except ServiceBackendError as e:
            raise rf_exceptions.ValidationError(str(e))
        else:
            resource_imported.send(
                sender=resource.__class__,
                instance=resource,
                plan=plan,
                offering=offering,
            )

        import_resource_executor = plugins.manager.get_import_resource_executor(
            offering.type
        )

        if import_resource_executor:
            transaction.on_commit(lambda: import_resource_executor.execute(resource))

        marketplace_resource = models.Resource.objects.get(scope=resource)
        resource_serializer = serializers.ResourceSerializer(
            marketplace_resource, context=self.get_serializer_context()
        )

        return Response(data=resource_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def update_attributes(self, request, uuid=None):
        offering = self.get_object()
        if not isinstance(request.data, dict):
            raise rf_exceptions.ValidationError('Dictionary is expected.')
        validate_attributes(request.data, offering.category)
        offering.attributes = request.data
        with reversion.create_revision():
            offering.save(update_fields=['attributes'])
            reversion.set_user(self.request.user)
            reversion.set_comment('Offering attributes have been updated via REST API')
        return Response(status=status.HTTP_200_OK)

    update_attributes_permissions = [permissions.user_is_owner_or_service_manager]
    update_attributes_validators = [validate_offering_update]

    def _update_action(self, request):
        offering = self.get_object()
        serializer = self.get_serializer(offering, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def update_location(self, request, uuid=None):
        return self._update_action(request)

    update_location_permissions = [permissions.user_is_owner_or_service_manager]
    update_location_validators = [validate_offering_update]
    update_location_serializer_class = serializers.OfferingLocationUpdateSerializer

    @action(detail=True, methods=['post'])
    def update_description(self, request, uuid=None):
        return self._update_action(request)

    update_description_permissions = [permissions.user_is_owner_or_service_manager]
    update_description_validators = [validate_offering_update]
    update_description_serializer_class = (
        serializers.OfferingDescriptionUpdateSerializer
    )

    @action(detail=True, methods=['post'])
    def update_overview(self, request, uuid=None):
        return self._update_action(request)

    update_overview_permissions = [permissions.user_is_owner_or_service_manager]
    update_overview_validators = [validate_offering_update]
    update_overview_serializer_class = serializers.OfferingOverviewUpdateSerializer

    @action(detail=True, methods=['post'])
    def update_options(self, request, uuid=None):
        return self._update_action(request)

    update_options_permissions = [permissions.user_is_owner_or_service_manager]
    update_options_validators = [validate_offering_update]
    update_options_serializer_class = serializers.OfferingOptionsUpdateSerializer

    @action(detail=True, methods=['post'])
    def update_secret_options(self, request, uuid=None):
        return self._update_action(request)

    update_secret_options_permissions = [permissions.user_is_owner_or_service_manager]
    update_secret_options_validators = [validate_offering_update]
    update_secret_options_serializer_class = (
        serializers.OfferingSecretOptionsUpdateSerializer
    )

    @action(detail=True, methods=['post'])
    def update_thumbnail(self, request, uuid=None):
        offering = self.get_object()
        serializer = serializers.OfferingThumbnailSerializer(
            instance=offering, data=request.data
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK)

    update_thumbnail_permissions = [permissions.user_can_update_thumbnail]

    @action(detail=True, methods=['post'])
    def delete_thumbnail(self, request, uuid=None):
        offering = self.get_object()
        offering.thumbnail = None
        offering.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    delete_thumbnail_permissions = update_thumbnail_permissions

    @action(detail=True)
    def customers(self, request, uuid):
        offering = self.get_object()
        active_customers = utils.get_active_customers(request, self)
        customer_queryset = utils.get_offering_customers(offering, active_customers)
        serializer_class = structure_serializers.CustomerSerializer
        serializer = serializer_class(
            instance=customer_queryset, many=True, context=self.get_serializer_context()
        )
        page = self.paginate_queryset(serializer.data)
        return self.get_paginated_response(page)

    customers_permissions = [structure_permissions.is_owner]

    def get_stats(self, get_queryset, serializer, serializer_context=None):
        offering = self.get_object()
        active_customers = utils.get_active_customers(self.request, self)
        start, end = utils.get_start_and_end_dates_from_request(self.request)
        invoice_items = invoice_models.InvoiceItem.objects.filter(
            resource__offering=offering,
            invoice__customer__in=active_customers,
            invoice__created__gte=start,
            invoice__created__lte=end,
        )
        queryset = get_queryset(invoice_items)
        serializer = serializer(
            instance=queryset, many=True, context=serializer_context
        )
        page = self.paginate_queryset(serializer.data)
        return self.get_paginated_response(page)

    @action(detail=True)
    def costs(self, *args, **kwargs):
        return self.get_stats(utils.get_offering_costs, serializers.CostsSerializer)

    costs_permissions = [structure_permissions.is_owner]

    @action(detail=True)
    def component_stats(self, *args, **kwargs):
        offering = self.get_object()
        offering_components_map = {
            component.type: component for component in offering.components.all()
        }

        def get_offering_component_stats(invoice_items):
            return (
                invoice_items.filter(
                    details__offering_component_type__in=offering_components_map.keys()
                )
                .values(
                    'details__offering_component_type',
                    'invoice__year',
                    'invoice__month',
                )
                .order_by(
                    'details__offering_component_type',
                    'invoice__year',
                    'invoice__month',
                )
                .annotate(total_quantity=Sum('quantity'))
            )

        serializer_context = {
            'offering_components_map': offering_components_map,
        }
        return self.get_stats(
            get_offering_component_stats,
            serializers.OfferingComponentStatSerializer,
            serializer_context,
        )

    component_stats_permissions = [structure_permissions.is_owner]

    @action(detail=True)
    def stats(self, *args, **kwargs):
        offering = self.get_object()
        resources_count = (
            models.Resource.objects.filter(offering=offering)
            .exclude(state=models.Resource.States.TERMINATED)
            .count()
        )
        customers_count = (
            models.Resource.objects.filter(offering=offering)
            .exclude(state=models.Resource.States.TERMINATED)
            .values('project__customer')
            .distinct()
            .count()
        )
        return Response(
            {
                'resources_count': resources_count,
                'customers_count': customers_count,
            },
            status=status.HTTP_200_OK,
        )

    stats_permissions = [structure_permissions.is_owner]

    @action(detail=True, methods=['post'])
    def update_divisions(self, request, uuid):
        offering = self.get_object()
        serializer = serializers.DivisionsSerializer(
            instance=offering, context={'request': request}, data=request.data
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK)

    update_divisions_permissions = [structure_permissions.is_owner]

    @action(detail=True, methods=['post'])
    def delete_divisions(self, request, uuid=None):
        offering = self.get_object()
        offering.divisions.clear()
        return Response(status=status.HTTP_204_NO_CONTENT)

    delete_divisions_permissions = update_divisions_permissions

    @action(detail=True, methods=['post'])
    def update_components(self, request, uuid=None):
        offering = self.get_object()
        serializer: serializers.OfferingComponentSerializer = self.get_serializer(
            data=request.data, many=True
        )
        serializer.is_valid(raise_exception=True)
        new_components = serializer.validated_data

        offering_update_serializer = serializers.OfferingUpdateSerializer(
            instance=offering
        )
        offering_update_serializer._update_components(offering, new_components)

        return Response(
            {'detail': _('The components of offering have been updated')},
            status=status.HTTP_200_OK,
        )

    update_components_permissions = [permissions.user_is_owner_or_service_manager]
    update_components_serializer_class = serializers.OfferingComponentSerializer

    @action(detail=True, methods=['post'])
    def add_endpoint(self, request, uuid=None):
        offering = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint = models.OfferingAccessEndpoint.objects.create(
            offering=offering,
            url=serializer.validated_data['url'],
            name=serializer.validated_data['name'],
        )

        return Response(
            {'uuid': endpoint.uuid},
            status=status.HTTP_201_CREATED,
        )

    add_endpoint_permissions = [permissions.user_is_owner_or_service_manager]
    add_endpoint_serializer_class = serializers.NestedEndpointSerializer

    @action(detail=True, methods=['post'])
    def delete_endpoint(self, request, uuid=None):
        offering = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        offering.endpoints.filter(uuid=serializer.validated_data['uuid']).delete()
        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

    delete_endpoint_serializer_class = serializers.EndpointDeleteSerializer
    delete_endpoint_permissions = [permissions.user_is_owner_or_service_manager]

    @action(detail=False, permission_classes=[], filter_backends=[DjangoFilterBackend])
    def groups(self, *args, **kwargs):
        OFFERING_LIMIT = 4
        qs = self.filter_queryset(
            self.get_queryset().filter(shared=True, state=models.Offering.States.ACTIVE)
        )
        customer_ids = self.paginate_queryset(
            qs.order_by('customer__name')
            .values_list('customer_id', flat=True)
            .distinct()
        )
        customers = {
            customer.id: customer
            for customer in structure_models.Customer.objects.filter(
                id__in=customer_ids
            )
        }
        return self.get_paginated_response(
            data=[
                {
                    'customer_name': customers[customer_id].name,
                    'customer_uuid': customers[customer_id].uuid.hex,
                    'offerings': [
                        {
                            'offering_name': offering.name,
                            'offering_uuid': offering.uuid.hex,
                        }
                        for offering in qs.filter(customer_id=customer_id)[
                            :OFFERING_LIMIT
                        ]
                    ],
                }
                for customer_id in customer_ids
            ]
        )

    @action(detail=True, methods=['GET'], renderer_classes=[PlainTextRenderer])
    def glauth_users_config(self, request, uuid=None):
        """
        This endpoint provides a config file for GLauth
        Example: https://github.com/glauth/glauth/blob/master/v2/sample-simple.cfg
        It is assumed that the config is used by an external agent,
        which synchronizes data from Waldur to GLauth
        """
        offering = self.get_object()

        if not offering.secret_options.get(
            'service_provider_can_create_offering_user', False
        ):
            logger.warning(
                "Offering %s doesn't have feature service_provider_can_create_offering_user enabled, skipping GLauth config generation",
                offering,
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data="Offering %s doesn't have feature service_provider_can_create_offering_user enabled"
                % offering,
            )

        offering_users = models.OfferingUser.objects.filter(offering=offering).exclude(
            username=''
        )

        offering_groups = models.OfferingUserGroup.objects.filter(offering=offering)

        user_records = utils.generate_glauth_records_for_offering_users(
            offering, offering_users
        )

        robot_accounts = models.RobotAccount.objects.filter(resource__offering=offering)

        robot_account_records = utils.generate_glauth_records_for_robot_accounts(
            offering, robot_accounts
        )

        other_group_records = []
        for group in offering_groups:
            gid = group.backend_metadata['gid']
            record = textwrap.dedent(
                f"""
                [[groups]]
                  name = "{gid}"
                  gidnumber = {gid}
            """
            )
            other_group_records.append(record)

        response_text = '\n'.join(
            user_records + robot_account_records + other_group_records
        )

        return Response(response_text)

    @action(detail=True, methods=['GET'])
    def user_has_resource_access(self, request, uuid=None):
        offering = self.get_object()
        username = request.query_params.get('username')
        if username is None:
            raise rf_exceptions.ValidationError(
                _('Username is missing in query parameters.')
            )

        try:
            user = core_models.User.objects.get(username=username)
        except core_models.User.DoesNotExist:
            error_message = _('The user with username %s does not exist!' % username)
            logger.error(error_message)
            raise rf_exceptions.ValidationError(error_message)

        has_access = utils.is_user_related_to_offering(offering, user)

        return Response(
            {'has_access': has_access},
            status=status.HTTP_200_OK,
        )


class PublicOfferingViewSet(rf_viewsets.ReadOnlyModelViewSet):
    queryset = models.Offering.objects.filter()
    lookup_field = 'uuid'
    serializer_class = serializers.PublicOfferingDetailsSerializer
    filterset_class = filters.OfferingFilter
    permission_classes = []

    def get_queryset(self):
        user = self.request.user
        return self.queryset.filter_by_ordering_availability_for_user(user)

    @action(detail=True, methods=['get'])
    def plans(self, request, uuid=None):
        offering = self.get_object()
        return Response(
            serializers.PublicOfferingDetailsSerializer(
                context=self.get_serializer_context()
            ).get_filtered_plans(offering),
            status=status.HTTP_200_OK,
        )

    def plan_detail(self, request, uuid=None, plan_uuid=None):
        offering = self.get_object()

        try:
            plan = utils.get_plans_available_for_user(
                offering=offering,
                user=request.user,
            ).get(uuid=plan_uuid)
            serializer = serializers.BasePublicPlanSerializer(
                plan, context=self.get_serializer_context()
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        except models.Plan.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


class OfferingReferralsViewSet(PublicViewsetMixin, rf_viewsets.ReadOnlyModelViewSet):
    queryset = pid_models.DataciteReferral.objects.all()
    serializer_class = serializers.OfferingReferralSerializer
    lookup_field = 'uuid'
    filter_backends = (
        filters.OfferingReferralScopeFilterBackend,
        structure_filters.GenericRoleFilter,
        DjangoFilterBackend,
    )
    filterset_class = filters.OfferingReferralFilter


class OfferingPermissionViewSet(structure_views.BasePermissionViewSet):
    queryset = models.OfferingPermission.objects.filter(is_active=True).order_by(
        '-created'
    )
    serializer_class = serializers.OfferingPermissionSerializer
    filter_backends = (
        structure_filters.GenericRoleFilter,
        DjangoFilterBackend,
    )
    filterset_class = filters.OfferingPermissionFilter
    scope_field = 'offering'


class OfferingPermissionLogViewSet(
    mixins.RetrieveModelMixin, mixins.ListModelMixin, rf_viewsets.GenericViewSet
):
    queryset = models.OfferingPermission.objects.filter(is_active=None).order_by(
        'offering__name'
    )
    serializer_class = serializers.OfferingPermissionLogSerializer
    filter_backends = (
        structure_filters.GenericRoleFilter,
        DjangoFilterBackend,
    )
    filterset_class = filters.OfferingPermissionFilter


class PlanUsageReporter:
    """
    This class provides aggregate counts of how many plans of a
    certain type for each offering is used.
    """

    def __init__(self, view, request):
        self.view = view
        self.request = request

    def get_report(self):
        plans = models.Plan.objects.exclude(offering__billable=False)

        query = self.parse_query()
        if query:
            plans = self.apply_filters(query, plans)

        resources = self.get_subquery()
        remaining = ExpressionWrapper(
            F('limit') - F('usage'), output_field=PositiveSmallIntegerField()
        )
        plans = plans.annotate(
            usage=Subquery(resources[:1]), limit=F('max_amount')
        ).annotate(remaining=remaining)
        plans = self.apply_ordering(plans)

        return self.serialize(plans)

    def parse_query(self):
        if self.request.query_params:
            serializer = serializers.PlanUsageRequestSerializer(
                data=self.request.query_params
            )
            serializer.is_valid(raise_exception=True)
            return serializer.validated_data
        return None

    def get_subquery(self):
        # Aggregate
        resources = (
            models.Resource.objects.filter(plan_id=OuterRef('pk'))
            .exclude(state=models.Resource.States.TERMINATED)
            .annotate(count=Count('*'))
            .order_by()
            .values_list('count', flat=True)
        )

        # Workaround for Django bug:
        # https://code.djangoproject.com/ticket/28296
        # It allows to remove extra GROUP BY clause from the subquery.
        resources.query.group_by = []

        return resources

    def apply_filters(self, query, plans):
        if query.get('offering_uuid'):
            plans = plans.filter(offering__uuid=query.get('offering_uuid'))

        if query.get('customer_provider_uuid'):
            plans = plans.filter(
                offering__customer__uuid=query.get('customer_provider_uuid')
            )

        return plans

    def apply_ordering(self, plans):
        param = (
            self.request.query_params and self.request.query_params.get('o') or '-usage'
        )
        return order_with_nulls(plans, param)

    def serialize(self, plans):
        page = self.view.paginate_queryset(plans)
        serializer = serializers.PlanUsageResponseSerializer(page, many=True)
        return self.view.get_paginated_response(serializer.data)


def validate_plan_update(plan):
    if models.Resource.objects.filter(plan=plan).exists():
        raise rf_exceptions.ValidationError(
            _('It is not possible to update plan because it is used by resources.')
        )


def validate_plan_archive(plan):
    if plan.archived:
        raise rf_exceptions.ValidationError(_('Plan is already archived.'))


class ProviderPlanViewSet(core_views.UpdateReversionMixin, BaseMarketplaceView):
    queryset = models.Plan.objects.all()
    serializer_class = serializers.ProviderPlanDetailsSerializer
    filterset_class = filters.PlanFilter
    filter_backends = (DjangoFilterBackend, filters.PlanFilterBackend)

    disabled_actions = ['destroy']
    update_validators = partial_update_validators = [validate_plan_update]

    archive_permissions = [structure_permissions.is_owner]
    archive_validators = [validate_plan_archive]

    @action(detail=True, methods=['post'])
    def archive(self, request, uuid=None):
        plan = self.get_object()
        with reversion.create_revision():
            plan.archived = True
            plan.save(update_fields=['archived'])
            reversion.set_user(self.request.user)
            reversion.set_comment('Plan has been archived.')
        return Response(
            {'detail': _('Plan has been archived.')}, status=status.HTTP_200_OK
        )

    @action(detail=False)
    def usage_stats(self, request):
        return PlanUsageReporter(self, request).get_report()

    @action(detail=True, methods=['post'])
    def update_divisions(self, request, uuid):
        plan = self.get_object()
        serializer = serializers.DivisionsSerializer(
            instance=plan, context={'request': request}, data=request.data
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK)

    update_divisions_permissions = [structure_permissions.is_owner]

    @action(detail=True, methods=['post'])
    def delete_divisions(self, request, uuid=None):
        plan = self.get_object()
        plan.divisions.clear()
        return Response(status=status.HTTP_204_NO_CONTENT)

    delete_divisions_permissions = update_divisions_permissions


class PlanComponentViewSet(PublicViewsetMixin, rf_viewsets.ReadOnlyModelViewSet):
    queryset = models.PlanComponent.objects.filter()
    serializer_class = serializers.PlanComponentSerializer
    filterset_class = filters.PlanComponentFilter
    lookup_field = 'uuid'

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_anonymous:
            return queryset.filter(
                plan__offering__shared=True, plan__divisions__isnull=True
            )
        elif user.is_staff or user.is_support:
            return queryset
        else:
            return queryset.filter(
                Q(plan__divisions__isnull=True) | Q(plan__divisions__in=user.divisions)
            )


# TODO: Remove after migration of clients to a new endpoint
class PublicPlanViewSet(rf_viewsets.ReadOnlyModelViewSet):
    queryset = models.Plan.objects.filter()
    serializer_class = serializers.PublicPlanDetailsSerializer
    filterset_class = filters.PlanFilter
    permission_classes = []
    lookup_field = 'uuid'

    def get_queryset(self):
        user = self.request.user
        return self.queryset.filter_by_plan_availability_for_user(user)


class ScreenshotViewSet(
    core_views.CreateReversionMixin,
    core_views.UpdateReversionMixin,
    BaseMarketplaceView,
):
    queryset = models.Screenshot.objects.all().order_by('offering__name')
    serializer_class = serializers.ScreenshotSerializer
    filterset_class = filters.ScreenshotFilter


class OrderViewSet(BaseMarketplaceView):
    queryset = models.Order.objects.all()
    serializer_class = serializers.OrderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.OrderFilter
    destroy_validators = partial_update_validators = [
        structure_utils.check_customer_blocked_or_archived
    ]

    def get_queryset(self):
        """
        Orders are available to both service provider and service consumer.
        """
        if self.request.user.is_staff or self.request.user.is_support:
            return self.queryset

        return self.queryset.filter(
            Q(
                project__permissions__user=self.request.user,
                project__permissions__is_active=True,
            )
            | Q(
                project__customer__permissions__user=self.request.user,
                project__customer__permissions__is_active=True,
            )
            | Q(
                items__offering__customer__permissions__user=self.request.user,
                items__offering__customer__permissions__is_active=True,
            )
        ).distinct()

    @action(detail=True, methods=['post'])
    def approve(self, request, uuid=None):
        tasks.approve_order(self.get_object(), request.user)

        return Response(
            {'detail': _('Order has been approved.')}, status=status.HTTP_200_OK
        )

    approve_validators = [
        core_validators.StateValidator(models.Order.States.REQUESTED_FOR_APPROVAL),
        structure_utils.check_customer_blocked_or_archived,
        structure_utils.check_project_end_date,
    ]
    approve_permissions = [permissions.user_can_approve_order_permission]

    @action(detail=True, methods=['post'])
    def reject(self, request, uuid=None):
        order = self.get_object()
        order.reject()
        order.save(update_fields=['state'])
        return Response(
            {'detail': _('Order has been rejected.')}, status=status.HTTP_200_OK
        )

    reject_validators = [
        core_validators.StateValidator(models.Order.States.REQUESTED_FOR_APPROVAL),
        structure_utils.check_customer_blocked_or_archived,
    ]
    reject_permissions = [permissions.user_can_reject_order]

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        structure_utils.check_customer_blocked_or_archived(project)
        structure_utils.check_project_end_date(project)

        super().perform_create(serializer)


class PluginViewSet(views.APIView):
    def get(self, request):
        offering_types = plugins.manager.get_offering_types()
        payload = []
        for offering_type in offering_types:
            components = [
                dict(
                    type=component.type,
                    name=component.name,
                    measured_unit=component.measured_unit,
                    billing_type=component.billing_type,
                )
                for component in plugins.manager.get_components(offering_type)
            ]
            payload.append(
                dict(
                    offering_type=offering_type,
                    components=components,
                    available_limits=plugins.manager.get_available_limits(
                        offering_type
                    ),
                )
            )
        return Response(payload, status=status.HTTP_200_OK)


class OrderItemViewSet(ConnectedOfferingDetailsMixin, BaseMarketplaceView):
    queryset = models.OrderItem.objects.all()
    filter_backends = (DjangoFilterBackend,)
    serializer_class = serializers.OrderItemDetailsSerializer
    filterset_class = filters.OrderItemFilter

    def order_items_destroy_validator(order_item):
        if not order_item:
            return
        if order_item.order.state != models.Order.States.REQUESTED_FOR_APPROVAL:
            raise rf_exceptions.PermissionDenied()

    destroy_validators = [order_items_destroy_validator]
    destroy_permissions = terminate_permissions = [
        structure_permissions.is_administrator
    ]

    def get_queryset(self):
        """
        OrderItems are available to both service provider and service consumer.
        """
        if self.request.user.is_staff or self.request.user.is_support:
            return self.queryset

        return self.queryset.filter(
            Q(
                order__project__permissions__user=self.request.user,
                order__project__permissions__is_active=True,
            )
            | Q(
                order__project__customer__permissions__user=self.request.user,
                order__project__customer__permissions__is_active=True,
            )
            | Q(
                offering__customer__permissions__user=self.request.user,
                offering__customer__permissions__is_active=True,
            )
        ).distinct()

    approve_permissions = (
        set_state_executing_permissions
    ) = (
        set_state_done_permissions
    ) = set_state_erred_permissions = cancel_termination_permissions = [
        permissions.can_approve_order_item
    ]

    reject_permissions = [permissions.can_reject_order_item]

    # Approve action is enabled for service provider, and
    # reject action is enabled for both provider and consumer.
    # Pending order item for remote offering is executed after it is approved by service provider.

    @action(detail=True, methods=['post'])
    def reject(self, request, uuid=None):
        order_item = self.get_object()

        if order_item.state == models.OrderItem.States.EXECUTING:
            if not order_item.resource:
                raise ValidationError('Order item does not have a resource.')
            callbacks.sync_order_item_state(
                order_item, models.OrderItem.States.TERMINATED
            )
        elif order_item.state == models.OrderItem.States.PENDING:
            order_item.reviewed_at = timezone.now()
            order_item.reviewed_by = request.user
            order_item.set_state_terminated(termination_comment="Order item rejected")
            order_item.save()
            if (
                order_item.order.state == models.Order.States.REQUESTED_FOR_APPROVAL
                and order_item.order.items.filter(
                    state=models.OrderItem.States.PENDING
                ).count()
                == 0
            ):
                order_item.order.reject()
                order_item.order.save()
        else:
            raise ValidationError('Order item is not in executing or pending state.')
        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def approve(self, request, uuid=None):
        order_item = self.get_object()

        if order_item.state == models.OrderItem.States.EXECUTING:
            # Basic marketplace resource case
            if not order_item.resource:
                raise ValidationError('Order item does not have a resource.')
            callbacks.sync_order_item_state(order_item, models.OrderItem.States.DONE)
        elif order_item.state == models.OrderItem.States.PENDING:
            # Marketplace remote or SLURM remote resource
            order_item.reviewed_at = timezone.now()
            order_item.reviewed_by = request.user
            order_item.set_state_executing()
            order_item.save()
            if (
                order_item.order.state == models.Order.States.REQUESTED_FOR_APPROVAL
                and order_item.order.items.filter(
                    state=models.OrderItem.States.PENDING
                ).count()
                == 0
            ):
                order_item.order.approve()
                order_item.order.save()
            transaction.on_commit(
                lambda: tasks.process_order_item.delay(
                    core_utils.serialize_instance(order_item),
                    core_utils.serialize_instance(request.user),
                )
            )
        else:
            raise ValidationError('Order item is not in executing or pending state.')
        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def terminate(self, request, uuid=None):
        order_item = self.get_object()
        if not plugins.manager.can_terminate_order_item(order_item.offering.type):
            return Response(
                {
                    'details': 'Order item could not be terminated because it is not supported by plugin.'
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # It is expected that plugin schedules Celery task to call backend
            # and then switches order item to terminated state.
            order_item.set_state_terminating()
            order_item.save(update_fields=['state'])
        except TransitionNotAllowed:
            return Response(
                {
                    'details': 'Order item could not be terminated because it has been already processed.'
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'details': 'Order item termination has been scheduled.'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'])
    def cancel_termination(self, request, uuid=None):
        from waldur_mastermind.marketplace_remote import PLUGIN_NAME, utils

        order_item = self.get_object()

        if order_item.type != models.OrderItem.Types.TERMINATE:
            raise ValidationError('This is not a termination order item.')

        if order_item.state != models.OrderItem.States.EXECUTING:
            raise ValidationError('This is not an executing order item.')

        if order_item.resource.offering.type != PLUGIN_NAME:
            raise ValidationError('This is not a remote order item.')

        client = utils.get_client_for_offering(order_item.resource.offering)

        try:
            remote_order = client.get_order(order_item.backend_id)
            remote_item = remote_order['items'][0]
            remote_item_uuid = remote_item['uuid']
            client.marketplace_order_item_reject(remote_item_uuid)
        except WaldurClientException as exc:
            raise ValidationError(exc)
        callbacks.sync_order_item_state(order_item, models.OrderItem.States.TERMINATED)

        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def set_state_executing(self, request, uuid=None):
        order_item = self.get_object()
        if order_item.offering.type not in [SLURM_REMOTE_PLUGIN_NAME]:
            raise rf_exceptions.MethodNotAllowed(
                _(
                    "The order item's offering with %s type does not support this action"
                    % order_item.offering.type
                )
            )

        if order_item.state not in [
            models.OrderItem.States.PENDING,
            models.OrderItem.States.ERRED,
        ]:
            raise rf_exceptions.ValidationError(
                _(
                    'Order item has incorrect state. Expected pending or erred, got %s'
                    % order_item.get_state_display()
                )
            )
        order_item.set_state_executing()
        order_item.save(update_fields=['state'])
        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def set_state_done(self, request, uuid=None):
        order_item = self.get_object()
        if order_item.offering.type not in [SLURM_REMOTE_PLUGIN_NAME]:
            raise rf_exceptions.MethodNotAllowed(
                _(
                    "The order item's offering with %s type does not support this action"
                    % order_item.offering.type
                )
            )

        if order_item.state != models.OrderItem.States.EXECUTING:
            raise rf_exceptions.ValidationError(
                _(
                    'Order item has incorrect state. Expected executing, got %s'
                    % order_item.get_state_display()
                )
            )
        callbacks.sync_order_item_state(order_item, models.OrderItem.States.DONE)
        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def set_state_erred(self, request, uuid=None):
        order_item = self.get_object()
        if order_item.offering.type not in [SLURM_REMOTE_PLUGIN_NAME]:
            raise rf_exceptions.MethodNotAllowed(
                _(
                    "The order item's offering with %s type does not support this action"
                    % order_item.offering.type
                )
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        error_message = serializer.validated_data['error_message']
        error_traceback = serializer.validated_data['error_traceback']

        callbacks.sync_order_item_state(order_item, models.OrderItem.States.ERRED)
        order_item.error_message = error_message
        order_item.error_traceback = error_traceback
        order_item.save(update_fields=['error_message', 'error_traceback'])
        return Response(status=status.HTTP_200_OK)

    set_state_erred_serializer_class = serializers.OrderItemSetStateErredSerializer


class CartItemViewSet(ConnectedOfferingDetailsMixin, core_views.ActionsViewSet):
    queryset = models.CartItem.objects.all()
    lookup_field = 'uuid'
    serializer_class = serializers.CartItemSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.CartItemFilter

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    @action(detail=False, methods=['post'])
    def submit(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        order_serializer = serializers.OrderSerializer(
            instance=order, context=self.get_serializer_context()
        )
        return Response(order_serializer.data, status=status.HTTP_201_CREATED)

    submit_serializer_class = serializers.CartSubmitSerializer


class ResourceViewSet(ConnectedOfferingDetailsMixin, core_views.ActionsViewSet):
    queryset = models.Resource.objects.all()
    filter_backends = (DjangoFilterBackend, filters.ResourceScopeFilterBackend)
    filterset_class = filters.ResourceFilter
    lookup_field = 'uuid'
    serializer_class = serializers.ResourceSerializer
    disabled_actions = ['create', 'destroy']
    update_serializer_class = (
        partial_update_serializer_class
    ) = serializers.ResourceUpdateSerializer

    def get_queryset(self):
        return self.queryset.filter_for_user(self.request.user)

    @action(detail=True, methods=['get'])
    def details(self, request, uuid=None):
        resource = self.get_object()
        if not resource.scope:
            return Response(status=status.HTTP_404_NOT_FOUND)
        resource_type = get_resource_type(resource.scope)
        serializer_class = get_resource_serializer_class(resource_type)
        if not serializer_class:
            return Response(status.HTTP_204_NO_CONTENT)
        serializer = serializer_class(
            instance=resource.scope, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def terminate(self, request, uuid=None):
        resource = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attributes = serializer.validated_data.get('attributes', {})

        with transaction.atomic():
            order_item = models.OrderItem(
                resource=resource,
                offering=resource.offering,
                type=models.OrderItem.Types.TERMINATE,
                attributes=attributes,
            )
            utils.validate_order_item(order_item, request)
            order = serializers.create_order(
                project=resource.project,
                user=self.request.user,
                items=[order_item],
                request=request,
            )

        return Response({'order_uuid': order.uuid.hex}, status=status.HTTP_200_OK)

    terminate_serializer_class = serializers.ResourceTerminateSerializer

    terminate_permissions = [permissions.user_can_terminate_resource]

    terminate_validators = [
        core_validators.StateValidator(
            models.Resource.States.OK, models.Resource.States.ERRED
        ),
        utils.check_customer_blocked_for_terminating,
        utils.check_pending_order_item_exists,
    ]

    @action(detail=True, methods=['post'])
    def switch_plan(self, request, uuid=None):
        resource = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.validated_data['plan']

        with transaction.atomic():
            order_item = models.OrderItem(
                resource=resource,
                offering=resource.offering,
                old_plan=resource.plan,
                plan=plan,
                type=models.OrderItem.Types.UPDATE,
                limits=resource.limits or {},
            )
            order = serializers.create_order(
                project=resource.project,
                user=self.request.user,
                items=[order_item],
                request=request,
            )

        return Response({'order_uuid': order.uuid.hex}, status=status.HTTP_200_OK)

    switch_plan_serializer_class = serializers.ResourceSwitchPlanSerializer

    @action(detail=True, methods=['post'])
    def update_limits(self, request, uuid=None):
        resource = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        limits = serializer.validated_data['limits']

        with transaction.atomic():
            order_item = models.OrderItem(
                resource=resource,
                offering=resource.offering,
                plan=resource.plan,
                type=models.OrderItem.Types.UPDATE,
                limits=limits,
                attributes={'old_limits': resource.limits},
            )
            order = serializers.create_order(
                project=resource.project,
                user=self.request.user,
                items=[order_item],
                request=request,
            )

        return Response({'order_uuid': order.uuid.hex}, status=status.HTTP_200_OK)

    update_limits_serializer_class = serializers.ResourceUpdateLimitsSerializer

    switch_plan_permissions = update_limits_permissions = [
        structure_permissions.is_administrator
    ]

    switch_plan_validators = update_limits_validators = [
        core_validators.StateValidator(models.Resource.States.OK),
        structure_utils.check_customer_blocked_or_archived,
        utils.check_pending_order_item_exists,
    ]

    @action(detail=True, methods=['get'])
    def plan_periods(self, request, uuid=None):
        resource = self.get_object()
        qs = models.ResourcePlanPeriod.objects.filter(resource=resource)
        qs = qs.filter(Q(end=None) | Q(end__gte=month_start(timezone.now())))
        serializer = serializers.ResourcePlanPeriodSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def move_resource(self, request, uuid=None):
        resource = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.validated_data['project']
        try:
            utils.move_resource(resource, project)
        except utils.MoveResourceException as exception:
            error_message = str(exception)
            return JsonResponse({'error_message': error_message}, status=409)

        serialized_resource = serializers.ResourceSerializer(
            resource, context=self.get_serializer_context()
        )

        return Response(serialized_resource.data, status=status.HTTP_200_OK)

    move_resource_serializer_class = serializers.MoveResourceSerializer
    move_resource_permissions = [structure_permissions.is_staff]

    @action(detail=True, methods=['post'])
    def set_backend_id(self, request, uuid=None):
        resource = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_backend_id = serializer.validated_data['backend_id']
        old_backend_id = resource.backend_id
        if new_backend_id != old_backend_id:
            resource.backend_id = serializer.validated_data['backend_id']
            resource.save()
            logger.info(
                '%s has changed backend_id from %s to %s',
                request.user.full_name,
                old_backend_id,
                new_backend_id,
            )

            return Response(
                {'status': _('Resource backend_id has been changed.')},
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {'status': _('Resource backend_id is not changed.')},
                status=status.HTTP_200_OK,
            )

    set_backend_id_permissions = [permissions.user_is_owner_or_service_manager]
    set_backend_id_serializer_class = serializers.ResourceBackendIDSerializer

    @action(detail=True, methods=['post'])
    def submit_report(self, request, uuid=None):
        resource = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource.report = serializer.validated_data['report']
        resource.save(update_fields=['report'])

        return Response({'status': _('Report is submitted')}, status=status.HTTP_200_OK)

    submit_report_permissions = [
        permissions.user_is_service_provider_owner_or_service_provider_manager
    ]
    submit_report_serializer_class = serializers.ResourceReportSerializer

    def _set_end_date(self, request, is_staff_action):
        resource = self.get_object()
        serializer = serializers.ResourceEndDateByProviderSerializer(
            data=request.data, instance=resource, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        transaction.on_commit(
            lambda: tasks.notify_about_resource_termination.delay(
                resource.uuid.hex, request.user.uuid.hex, is_staff_action
            )
        )

        if not is_staff_action:
            log.log_marketplace_resource_end_date_has_been_updated_by_provider(
                resource, request.user
            )
        else:
            log.log_marketplace_resource_end_date_has_been_updated_by_staff(
                resource, request.user
            )

        return Response(status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def set_end_date_by_provider(self, request, uuid=None):
        return self._set_end_date(request, False)

    set_end_date_by_provider_permissions = [
        permissions.user_can_set_end_date_by_provider
    ]

    @action(detail=True, methods=['post'])
    def set_end_date_by_staff(self, request, uuid=None):
        return self._set_end_date(request, True)

    set_end_date_by_staff_permissions = [structure_permissions.is_staff]

    # Service provider endpoint only
    @action(detail=True, methods=['get'])
    def team(self, request, uuid=None):
        resource = self.get_object()
        project = resource.project

        return Response(
            ProjectUserSerializer(
                instance=project.get_users(),
                many=True,
                context={'project': project, 'request': request},
            ).data,
            status=status.HTTP_200_OK,
        )

    team_permissions = [
        permissions.user_is_service_provider_owner_or_service_provider_manager
    ]

    # Service provider endpoint only
    @action(detail=True, methods=['post'])
    def downscaling_request_completed(self, request, uuid=None):
        resource = self.get_object()
        resource.requested_downscaling = False
        resource.save()
        logger.info(
            "Downscaling request for resource %s completed",
            resource,
        )
        log.log_resource_downscaled(resource)

        return Response(status=status.HTTP_200_OK)

    downscaling_request_completed_permissions = [
        permissions.user_is_service_provider_owner_or_service_provider_manager
    ]

    def downscaling_is_requested(obj):
        if not obj.requested_downscaling:
            raise ValidationError('Downscaling has not been requested.')

    downscaling_request_completed_validators = [downscaling_is_requested]

    @action(detail=True, methods=['get'])
    def offering_for_subresources(self, request, uuid=None):
        resource = self.get_object()

        try:
            service_settings = structure_models.ServiceSettings.objects.get(
                scope=resource.scope,
            )
        except structure_models.ServiceSettingsDoesNotExist:
            return Response([])

        offerings = models.Offering.objects.filter(scope=service_settings)
        result = [
            {'uuid': offering.uuid.hex, 'type': offering.type} for offering in offerings
        ]
        return Response(result)

    @action(detail=True, methods=['get'], renderer_classes=[PlainTextRenderer])
    def glauth_users_config(self, request, uuid=None):
        resource: models.Resource = self.get_object()
        project = resource.project
        offering = resource.offering

        if not offering.secret_options.get(
            'service_provider_can_create_offering_user', False
        ):
            logger.warning(
                "Offering %s doesn't have feature service_provider_can_create_offering_user enabled, skipping GLauth config generation",
                offering,
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data="Offering %s doesn't have feature service_provider_can_create_offering_user enabled"
                % offering,
            )

        user_ids = structure_models.ProjectPermission.objects.filter(
            project=project, is_active=True
        ).values_list('user_id')

        offering_users = models.OfferingUser.objects.filter(
            offering=offering,
            user__id__in=user_ids,
        ).exclude(username='')

        offering_groups = models.OfferingUserGroup.objects.filter(offering=offering)

        user_records = utils.generate_glauth_records_for_offering_users(
            offering, offering_users
        )

        robot_accounts = models.RobotAccount.objects.filter(resource__offering=offering)

        robot_account_records = utils.generate_glauth_records_for_robot_accounts(
            offering, robot_accounts
        )

        other_group_records = []
        for group in offering_groups:
            gid = group.backend_metadata['gid']
            record = textwrap.dedent(
                f"""
                [[groups]]
                  name = "{gid}"
                  gidnumber = {gid}
            """
            )
            other_group_records.append(record)

        response_text = '\n'.join(
            user_records + robot_account_records + other_group_records
        )

        return Response(response_text)


class ProjectChoicesViewSet(ListAPIView):
    def get_project(self):
        project_uuid = self.kwargs['project_uuid']
        if not is_uuid_like(project_uuid):
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data='Project UUID is invalid.'
            )
        return get_object_or_404(structure_models.Project, uuid=project_uuid)

    def get_category(self):
        category_uuid = self.kwargs['category_uuid']
        if not is_uuid_like(category_uuid):
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data='Category UUID is invalid.'
            )
        return get_object_or_404(models.Category, uuid=category_uuid)


class ResourceOfferingsViewSet(ProjectChoicesViewSet):
    serializer_class = serializers.ResourceOfferingSerializer

    def get_queryset(self):
        project = self.get_project()
        category = self.get_category()
        offerings = (
            models.Resource.objects.filter(project=project, offering__category=category)
            .exclude(state=models.Resource.States.TERMINATED)
            .values_list('offering_id', flat=True)
        )
        return models.Offering.objects.filter(pk__in=offerings)


class RuntimeStatesViewSet(views.APIView):
    def get(self, request, project_uuid):
        projects = filter_queryset_for_user(
            structure_models.Project.objects.all(), request.user
        )
        project = get_object_or_404(projects, uuid=project_uuid)
        resources = models.Resource.objects.filter(project=project)
        category_uuid = request.query_params.get('category_uuid')
        if category_uuid and is_uuid_like(category_uuid):
            resources = resources.filter(offering__category__uuid=category_uuid)
        runtime_states = set(
            resources.values_list(
                'backend_metadata__runtime_state', flat=True
            ).distinct()
        )
        result = sorted(
            [
                {"value": state, "label": state.lower()}
                for state in runtime_states
                if state
            ],
            key=lambda option: option['value'],
        )
        return Response(result)


class RelatedCustomersViewSet(ListAPIView):
    serializer_class = structure_serializers.BasicCustomerSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = structure_filters.NameFilterSet

    def get_customer(self):
        customer_uuid = self.kwargs['customer_uuid']
        if not is_uuid_like(customer_uuid):
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data='Customer UUID is invalid.'
            )
        qs = filter_queryset_for_user(
            structure_models.Customer.objects.all(), self.request.user
        )
        return get_object_or_404(qs, uuid=customer_uuid)

    def get_queryset(self):
        customer = self.get_customer()
        customer_ids = (
            models.Resource.objects.all()
            .filter_for_user(self.request.user)
            .filter(offering__customer=customer)
            .values_list('project__customer_id', flat=True)
            .distinct()
        )
        return structure_models.Customer.objects.filter(id__in=customer_ids)


class CategoryComponentUsageViewSet(core_views.ReadOnlyActionsViewSet):
    queryset = models.CategoryComponentUsage.objects.all().order_by(
        '-date', 'component__type'
    )
    filter_backends = (
        DjangoFilterBackend,
        filters.CategoryComponentUsageScopeFilterBackend,
    )
    filterset_class = filters.CategoryComponentUsageFilter
    serializer_class = serializers.CategoryComponentUsageSerializer


class ComponentUsageViewSet(core_views.ReadOnlyActionsViewSet):
    queryset = models.ComponentUsage.objects.all().order_by('-date', 'component__type')
    filter_backends = (structure_filters.GenericRoleFilter, DjangoFilterBackend)
    filterset_class = filters.ComponentUsageFilter
    serializer_class = serializers.ComponentUsageSerializer

    @action(detail=False, methods=['post'])
    def set_usage(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource = serializer.validated_data['plan_period'].resource
        if not _has_owner_access(
            request.user, resource.offering.customer
        ) and not resource.offering.has_user(request.user):
            raise PermissionDenied(
                _(
                    'Only staff, service provider owner and service manager are allowed '
                    'to submit usage data for marketplace resource.'
                )
            )
        serializer.save()
        return Response(status=status.HTTP_201_CREATED)

    set_usage_serializer_class = serializers.ComponentUsageCreateSerializer


class MarketplaceAPIViewSet(rf_viewsets.ViewSet):
    """
    TODO: Move this viewset to  ComponentUsageViewSet.
    """

    permission_classes = ()
    serializer_class = serializers.ServiceProviderSignatureSerializer

    def get_validated_data(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data['data']
        dry_run = serializer.validated_data['dry_run']

        if self.action == 'set_usage':
            data_serializer = serializers.ComponentUsageCreateSerializer(
                data=data, context={'request': request}
            )
            data_serializer.is_valid(raise_exception=True)
            if not dry_run:
                data_serializer.save()

        return serializer.validated_data, dry_run

    @action(detail=False, methods=['post'])
    @csrf_exempt
    def check_signature(self, request, *args, **kwargs):
        self.get_validated_data(request)
        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    @csrf_exempt
    def set_usage(self, request, *args, **kwargs):
        self.get_validated_data(request)
        return Response(status=status.HTTP_201_CREATED)


class OfferingFileViewSet(core_views.ActionsViewSet):
    queryset = models.OfferingFile.objects.all().order_by('name')
    filterset_class = filters.OfferingFileFilter
    filter_backends = [DjangoFilterBackend]
    serializer_class = serializers.OfferingFileSerializer
    lookup_field = 'uuid'
    disabled_actions = ['update', 'partial_update']

    def check_create_permissions(request, view, obj=None):
        serializer = view.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        offering = serializer.validated_data['offering']

        if user.is_staff or (
            offering.customer
            and offering.customer.has_user(user, structure_models.CustomerRole.OWNER)
        ):
            return

        raise rf_exceptions.PermissionDenied()

    create_permissions = [check_create_permissions]
    destroy_permissions = [structure_permissions.is_owner]


class OfferingUsersViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    rf_viewsets.GenericViewSet,
):
    queryset = models.OfferingUser.objects.all()
    serializer_class = serializers.OfferingUserSerializer
    lookup_field = 'uuid'
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.OfferingUserFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        current_user = self.request.user
        if current_user.is_staff or current_user.is_support:
            return queryset

        project_permissions = structure_models.ProjectPermission.objects.filter(
            user=current_user, is_active=True
        )
        project_ids = project_permissions.values_list('project_id', flat=True)
        customer_permissions = structure_models.CustomerPermission.objects.filter(
            user=current_user, is_active=True
        )
        customer_ids = customer_permissions.values_list('customer_id', flat=True)
        all_customer_ids = set(customer_ids) | set(
            structure_models.Project.objects.filter(id__in=project_ids).values_list(
                'customer_id', flat=True
            )
        )
        division_ids = structure_models.Customer.objects.filter(
            id__in=all_customer_ids
        ).values_list('division_id', flat=True)

        queryset = queryset.filter(
            # Exclude offerings with disabled OfferingUsers feature
            Q(offering__secret_options__service_provider_can_create_offering_user=True)
            &
            # user can see own remote offering user
            (
                Q(user=current_user)
                | (
                    (
                        # service provider can see all records related to managed offerings
                        Q(
                            offering__customer__permissions__user=current_user,
                            offering__customer__permissions__is_active=True,
                        )
                        # users with project permission are visible to other users in the same project
                        | Q(
                            user__projectpermission__project__in=project_ids,
                            user__projectpermission__is_active=True,
                        )
                        # users with customer permission are visible to other users in the same customer
                        | Q(
                            user__customerpermission__customer__in=customer_ids,
                            user__customerpermission__is_active=True,
                        )
                        # users with project permission are visible to other users in the same customer
                        | Q(
                            user__projectpermission__project__customer__in=customer_ids,
                            user__projectpermission__is_active=True,
                        )
                    )
                    & (
                        # only offerings managed by customer where the current user has a role
                        Q(offering__customer__id__in=all_customer_ids)
                        |
                        # only offerings from divisions including the current user's customers
                        Q(offering__divisions__in=division_ids)
                    )
                )
            )
        ).distinct()
        return queryset


class OfferingUserGroupViewSet(core_views.ActionsViewSet):
    queryset = models.OfferingUserGroup.objects.all()
    serializer_class = serializers.OfferingUserGroupDetailsSerializer
    lookup_field = 'uuid'
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.OfferingUserGroupFilter
    create_serializer_class = (
        update_serializer_class
    ) = partial_update_serializer_class = serializers.OfferingUserGroupSerializer

    unsafe_methods_permissions = [
        permissions.user_is_service_provider_owner_or_service_provider_manager
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        current_user = self.request.user
        if current_user.is_staff or current_user.is_support:
            return queryset

        customers = structure_models.CustomerPermission.objects.filter(
            user=current_user, is_active=True
        ).values_list('customer_id')
        projects = structure_models.ProjectPermission.objects.filter(
            user=current_user, is_active=True
        ).values_list('project_id')
        subquery = (
            Q(projects__customer__in=customers)
            | Q(offering__customer__in=customers)
            | Q(projects__in=projects)
        )
        return queryset.filter(subquery)

    def perform_create(self, serializer):
        offering_group: models.OfferingUserGroup = serializer.save()
        offering = offering_group.offering
        offering_groups = models.OfferingUserGroup.objects.filter(offering=offering)

        existing_ids = offering_groups.filter(
            backend_metadata__has_key='gid'
        ).values_list('backend_metadata__gid', flat=True)

        if len(existing_ids) == 0:
            max_group_id = int(
                offering.plugin_options.get('initial_usergroup_number', 6000)
            )
        else:
            max_group_id = max(existing_ids)

        offering_group.backend_metadata['gid'] = max_group_id + 1
        offering_group.save(update_fields=['backend_metadata'])


class StatsViewSet(rf_viewsets.ViewSet):
    permission_classes = [rf_permissions.IsAuthenticated, core_permissions.IsSupport]

    @action(detail=False, methods=['get'])
    def organization_project_count(self, request, *args, **kwargs):
        data = structure_models.Project.available_objects.values(
            'customer__abbreviation', 'customer__name', 'customer__uuid'
        ).annotate(count=Count('customer__uuid'))
        serializer = serializers.CustomerStatsSerializer(data, many=True)
        return Response(status=status.HTTP_200_OK, data=serializer.data)

    @action(detail=False, methods=['get'])
    def organization_resource_count(self, request, *args, **kwargs):
        data = (
            models.Resource.objects.filter(state=models.Resource.States.OK)
            .values(
                'project__customer__abbreviation',
                'project__customer__name',
                'project__customer__uuid',
            )
            .annotate(count=Count('project__customer__uuid'))
        )
        serializer = serializers.CustomerStatsSerializer(data, many=True)
        return Response(status=status.HTTP_200_OK, data=serializer.data)

    @action(detail=False, methods=['get'])
    def customer_member_count(self, request, *args, **kwargs):
        active_customer_ids = models.Resource.objects.filter(
            state__in=(models.Resource.States.OK, models.Resource.States.UPDATING)
        ).values('project__customer_id')

        has_resources = []

        customers = (
            structure_models.CustomerPermission.objects.filter(
                is_active=True, customer_id__in=active_customer_ids
            )
            .values('customer__abbreviation', 'customer__name', 'customer__uuid')
            .annotate(count=Count('customer__uuid'))
        )

        projects = (
            structure_models.ProjectPermission.objects.filter(
                is_active=True, project__customer_id__in=active_customer_ids
            )
            .values(
                'project__customer__abbreviation',
                'project__customer__name',
                'project__customer__uuid',
            )
            .annotate(count=Count('project__customer__uuid'))
        )

        for c in serializers.CustomerStatsSerializer(customers, many=True).data:
            c['has_resources'] = True
            c['count'] += sum(
                [
                    p['count']
                    for p in projects
                    if p['project__customer__uuid'] == c['uuid']
                ]
            )
            has_resources.append(c)

        has_not_resources = []

        customers = (
            structure_models.CustomerPermission.objects.filter(is_active=True)
            .exclude(customer_id__in=active_customer_ids)
            .values('customer__abbreviation', 'customer__name', 'customer__uuid')
            .annotate(count=Count('customer__uuid'))
        )

        projects = (
            structure_models.ProjectPermission.objects.filter(is_active=True)
            .exclude(project__customer_id__in=active_customer_ids)
            .values(
                'project__customer__abbreviation',
                'project__customer__name',
                'project__customer__uuid',
            )
            .annotate(count=Count('project__customer__uuid'))
        )

        for c in serializers.CustomerStatsSerializer(customers, many=True).data:
            c['has_resources'] = False
            c['count'] += sum(
                [
                    p['count']
                    for p in projects
                    if p['project__customer__uuid'] == c['uuid']
                ]
            )
            has_not_resources.append(c)

        return Response(
            status=status.HTTP_200_OK, data=has_resources + has_not_resources
        )

    @action(detail=False, methods=['get'])
    def resources_limits(self, request, *args, **kwargs):
        data = []

        for resource in (
            models.Resource.objects.filter(state=models.Resource.States.OK)
            .exclude(limits={})
            .values('offering__uuid', 'limits')
        ):
            limits = resource['limits']

            for name, value in limits.items():
                if value > 0:
                    try:
                        prev = next(
                            filter(
                                lambda x: x['offering_uuid']
                                == resource['offering__uuid']
                                and x['name'] == name,
                                data,
                            )
                        )
                    except StopIteration:
                        prev = None

                    if not prev:
                        data.append(
                            {
                                'offering_uuid': resource['offering__uuid'],
                                'name': name,
                                'value': value,
                            }
                        )
                    else:
                        prev['value'] += value

        return Response(
            self._expand_result_with_information_of_divisions(data),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def component_usages(self, request, *args, **kwargs):
        now = timezone.now()
        data = (
            models.ComponentUsage.objects.filter(
                billing_period__year=now.year, billing_period__month=now.month
            )
            .values('resource__offering__uuid', 'component__type')
            .annotate(usage=Sum('usage'))
        )
        serializer = serializers.ComponentUsagesStatsSerializer(data, many=True)
        return Response(
            self._expand_result_with_information_of_divisions(serializer.data),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def component_usages_per_project(self, request, *args, **kwargs):
        now = timezone.now()
        data = (
            models.ComponentUsage.objects.filter(
                billing_period__year=now.year, billing_period__month=now.month
            )
            .annotate(
                project_uuid=F('resource__project__uuid'),
                component_type=F('component__type'),
            )
            .values('project_uuid', 'component_type')
            .annotate(usage=Sum('usage'))
        )
        return Response(
            data,
            status=status.HTTP_200_OK,
        )

    # cache for 1 hour
    @method_decorator(cache_page(60 * 60))
    @action(detail=False, methods=['get'])
    def component_usages_per_month(self, request, *args, **kwargs):
        start, end = utils.get_start_and_end_dates_from_request(self.request)
        usages = models.ComponentUsage.objects.filter(
            billing_period__gte=start, billing_period__lte=end
        )

        data = usages.values(
            'resource__offering__uuid',
            'component__type',
            'billing_period__year',
            'billing_period__month',
        ).annotate(usage=Sum('usage'))
        serializer = serializers.ComponentUsagesPerMonthStatsSerializer(data, many=True)
        return Response(
            self._expand_result_with_information_of_divisions(serializer.data),
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _expand_result_with_information_of_divisions(result):
        data_with_divisions = []

        for record in result:
            offering = models.Offering.objects.get(uuid=record['offering_uuid'])
            record['offering_country'] = offering.country or offering.customer.country
            divisions = offering.divisions.all()

            if not divisions:
                new_data = copy.copy(record)
                new_data['division_name'] = ''
                new_data['division_uuid'] = ''
                data_with_divisions.append(new_data)
            else:
                for division in divisions:
                    new_data = copy.copy(record)
                    new_data['division_name'] = division.name
                    new_data['division_uuid'] = division.uuid.hex
                    data_with_divisions.append(new_data)

        return data_with_divisions

    @action(detail=False, methods=['get'])
    def count_users_of_service_providers(self, request, *args, **kwargs):
        result = []

        for sp in models.ServiceProvider.objects.all().select_related(
            'customer', 'customer__division'
        ):
            data = {
                'count': utils.get_service_provider_user_ids(
                    self.request.user, sp
                ).count()
            }
            data.update(self._get_service_provider_info(sp))
            result.append(data)

        return Response(
            result,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def count_projects_of_service_providers(self, request, *args, **kwargs):
        result = []

        for sp in models.ServiceProvider.objects.all().select_related(
            'customer', 'customer__division'
        ):
            data = {'count': utils.get_service_provider_project_ids(sp).count()}
            data.update(self._get_service_provider_info(sp))
            result.append(data)

        return Response(
            result,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def count_projects_of_service_providers_grouped_by_oecd(
        self, request, *args, **kwargs
    ):
        result = []

        for sp in models.ServiceProvider.objects.all().select_related(
            'customer', 'customer__division'
        ):
            project_ids = utils.get_service_provider_project_ids(sp)
            projects = (
                structure_models.Project.available_objects.filter(id__in=project_ids)
                .values('oecd_fos_2007_code')
                .annotate(count=Count('id'))
            )

            for p in projects:
                data = {
                    'count': p['count'],
                    'oecd_fos_2007_code': p['oecd_fos_2007_code'],
                }
                data.update(self._get_service_provider_info(sp))
                result.append(data)

        return Response(
            self._expand_result_with_oecd_name(result), status=status.HTTP_200_OK
        )

    def _projects_usages_grouped_by_field(self, field_name):
        results = {}

        for project in structure_models.Project.objects.all():
            field_value = str(getattr(project, field_name))
            if field_value in results:
                results[field_value]['projects_ids'].append(project.id)
            else:
                results[field_value] = {
                    'projects_ids': [project.id],
                }

        now = timezone.now()

        for key, result in results.items():
            ids = result.pop('projects_ids')
            usages = (
                models.ComponentUsage.objects.filter(
                    billing_period__year=now.year,
                    billing_period__month=now.month,
                    resource__project__id__in=ids,
                )
                .values('component__type')
                .annotate(usage=Sum('usage'))
            )

            for usage in usages:
                result[usage['component__type']] = usage['usage']

        return results

    @action(detail=False, methods=['get'])
    def projects_usages_grouped_by_oecd(self, request, *args, **kwargs):
        return Response(
            self._replace_keys_from_oecd_code_to_oecd_name(
                self._projects_usages_grouped_by_field('oecd_fos_2007_code')
            ),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def projects_usages_grouped_by_industry_flag(self, request, *args, **kwargs):
        return Response(
            self._projects_usages_grouped_by_field('is_industry'),
            status=status.HTTP_200_OK,
        )

    def _projects_limits_grouped_by_field(self, field_name):
        results = {}

        for project in structure_models.Project.objects.all():
            field_value = str(getattr(project, field_name))
            if field_value in results:
                results[field_value]['projects_ids'].append(project.id)
            else:
                results[field_value] = {
                    'projects_ids': [project.id],
                }

        for key, result in results.items():
            ids = result.pop('projects_ids')

            for resource in (
                models.Resource.objects.filter(
                    state=models.Resource.States.OK, project__id__in=ids
                )
                .exclude(limits={})
                .values('offering__uuid', 'limits')
            ):
                limits = resource['limits']

                for name, value in limits.items():
                    if value > 0:
                        if name in result:
                            result[name] += value
                        else:
                            result[name] = value

        return results

    @action(detail=False, methods=['get'])
    def projects_limits_grouped_by_oecd(self, request, *args, **kwargs):
        return Response(
            self._replace_keys_from_oecd_code_to_oecd_name(
                self._projects_limits_grouped_by_field('oecd_fos_2007_code')
            ),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def projects_limits_grouped_by_industry_flag(self, request, *args, **kwargs):
        return Response(
            self._projects_limits_grouped_by_field('is_industry'),
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def total_cost_of_active_resources_per_offering(self, request, *args, **kwargs):
        start, end = utils.get_start_and_end_dates_from_request(self.request)
        invoice_items = (
            invoice_models.InvoiceItem.objects.filter(
                invoice__created__gte=start,
                invoice__created__lte=end,
            )
            .values('resource__offering__uuid')
            .annotate(
                cost=Sum(
                    (Ceil(F('quantity') * F('unit_price') * 100) / 100),
                    output_field=FloatField(),
                )
            )
        )

        serializer = serializers.OfferingCostSerializer(invoice_items, many=True)

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _get_service_provider_info(service_provider):
        return {
            'service_provider_uuid': service_provider.uuid.hex,
            'customer_uuid': service_provider.customer.uuid.hex,
            'customer_name': service_provider.customer.name,
            'customer_division_uuid': service_provider.customer.division.uuid.hex
            if service_provider.customer.division
            else '',
            'customer_division_name': service_provider.customer.division.name
            if service_provider.customer.division
            else '',
        }

    @staticmethod
    def _expand_result_with_oecd_name(data):
        if not hasattr(data, '__iter__'):
            return data

        for d in data:
            if not isinstance(d, dict):
                return data

            if 'oecd_fos_2007_code' in d.keys():
                name = [
                    c[1]
                    for c in structure_models.Project.OECD_FOS_2007_CODES
                    if c[0] == d['oecd_fos_2007_code']
                ]
                if name:
                    d['oecd_fos_2007_name'] = name[0]
                else:
                    d['oecd_fos_2007_name'] = ''

        return data

    @staticmethod
    def _replace_keys_from_oecd_code_to_oecd_name(data):
        if not isinstance(data, dict):
            return data

        results = {}
        for code, value in data.items():
            name = [
                c[1]
                for c in structure_models.Project.OECD_FOS_2007_CODES
                if c[0] == code
            ]
            if name:
                results[f'{code} {str(name[0])}'] = value
            else:
                results[code] = value

        return results

    @action(detail=False, methods=['get'])
    def count_unique_users_connected_with_active_resources_of_service_provider(
        self, request, *args, **kwargs
    ):
        resources = self.get_active_resources()

        result = {}

        for resource in resources:
            if not resource.offering.customer:
                continue

            key = resource.offering.customer.uuid.hex
            user_ids = set(
                structure_models.ProjectPermission.objects.filter(
                    project=resource.project, is_active=True
                ).values_list('user_id', flat=True)
            )

            if key in result:
                user_ids |= result[key]['user_ids']

            result[key] = {
                'user_ids': user_ids,
                'customer_uuid': resource.offering.customer.uuid.hex,
                'customer_name': resource.offering.customer.name,
                'count_users': len(user_ids),
            }

        result = list(result.values())

        [r.pop('user_ids') for r in result]

        return Response(
            result,
            status=status.HTTP_200_OK,
        )

    def get_active_resources(self):
        return models.Resource.objects.filter(
            state__in=(
                models.Resource.States.OK,
                models.Resource.States.UPDATING,
                models.Resource.States.TERMINATING,
            )
        )

    @action(detail=False, methods=['get'])
    def count_active_resources_grouped_by_offering(self, request, *args, **kwargs):
        result = (
            self.get_active_resources()
            .values('offering__uuid', 'offering__name', 'offering__country')
            .annotate(count=Count('id'))
            .order_by()
        )

        return Response(
            serializers.OfferingStatsSerializer(result, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def count_active_resources_grouped_by_offering_country(
        self, request, *args, **kwargs
    ):
        result = (
            self.get_active_resources()
            .values('offering__country')
            .annotate(count=Count('id'))
            .order_by()
        )

        return Response(
            serializers.OfferingCountryStatsSerializer(result, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def count_active_resources_grouped_by_division(self, request, *args, **kwargs):
        result = (
            self.get_active_resources()
            .values(
                'offering__customer__division__name',
                'offering__customer__division__uuid',
            )
            .annotate(count=Count('id'))
            .order_by()
        )

        return Response(
            serializers.CountStatsSerializer(result, many=True).data,
            status=status.HTTP_200_OK,
        )

    def _get_count_projects_with_active_resources_grouped_by_provider_and_field(
        self, grouped_field
    ):
        return (
            structure_models.Project.objects.filter(is_removed=False)
            .filter(
                resource__state__in=(
                    models.Resource.States.OK,
                    models.Resource.States.UPDATING,
                    models.Resource.States.TERMINATING,
                )
            )
            .values(
                'resource__offering__customer__name',
                'resource__offering__customer__abbreviation',
                'resource__offering__customer__uuid',
                grouped_field,
            )
            .annotate(count=Count('id'))
            .order_by('resource__offering__customer__name')
        )

    @action(detail=False, methods=['get'])
    def count_projects_grouped_by_provider_and_oecd(self, request, *args, **kwargs):
        result = self._get_count_projects_with_active_resources_grouped_by_provider_and_field(
            'oecd_fos_2007_code'
        )
        result = self._expand_result_with_oecd_name(result)
        return Response(
            serializers.CustomerOecdCodeStatsSerializer(result, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'])
    def count_projects_grouped_by_provider_and_industry_flag(
        self, request, *args, **kwargs
    ):
        result = self._get_count_projects_with_active_resources_grouped_by_provider_and_field(
            'is_industry'
        )
        return Response(
            serializers.CustomerIndustryFlagStatsSerializer(result, many=True).data,
            status=status.HTTP_200_OK,
        )


class ProviderInvoiceItemsViewSet(core_views.ReadOnlyActionsViewSet):
    queryset = invoice_models.InvoiceItem.objects.all().order_by('-invoice__created')
    filter_backends = (
        DjangoFilterBackend,
        filters.MarketplaceInvoiceItemsFilterBackend,
    )
    filterset_class = filters.MarketplaceInvoiceItemsFilter
    serializer_class = invoice_serializers.InvoiceItemSerializer


for view in (structure_views.ProjectCountersView, structure_views.CustomerCountersView):

    def inject_resources_counter(scope):
        counters = models.AggregateResourceCount.objects.filter(scope=scope).only(
            'count', 'category'
        )
        return {
            f'marketplace_category_{counter.category.uuid}': counter.count
            for counter in counters
        }

    view.register_dynamic_counter(inject_resources_counter)


def can_mutate_robot_account(request, view, obj=None):
    if not obj:
        return
    if obj.backend_id:
        raise PermissionDenied('Remote robot account is synchronized.')
    if request.user.is_staff:
        return
    if obj.resource.offering.customer.has_user(request.user):
        return
    raise PermissionDenied(
        'Only staff, service provider owner and '
        'service provider manager can add, remove or update robot accounts'
    )


class RobotAccountViewSet(core_views.ActionsViewSet):
    queryset = models.RobotAccount.objects.all()
    lookup_field = 'uuid'
    create_serializer_class = serializers.RobotAccountSerializer
    update_serializer_class = serializers.RobotAccountSerializer
    serializer_class = serializers.RobotAccountDetailsSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.RobotAccountFilter

    unsafe_methods_permissions = [can_mutate_robot_account]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_staff or user.is_support:
            return qs
        customers = structure_models.CustomerPermission.objects.filter(
            user=user, is_active=True
        ).values_list('customer_id')
        projects = structure_models.ProjectPermission.objects.filter(
            user=user, is_active=True
        ).values_list('project_id')
        subquery = (
            Q(resource__project__in=projects)
            | Q(resource__project__customer__in=customers)
            | Q(resource__offering__customer__in=customers)
        )
        return qs.filter(subquery)

    def perform_create(self, serializer):
        instance = serializer.save()
        offering = instance.resource.offering
        utils.setup_linux_related_data(instance, offering)
        instance.save()

    def perform_update(self, serializer):
        instance = serializer.save()
        offering = instance.resource.offering
        utils.setup_linux_related_data(instance, offering)
        instance.save()


class SectionViewSet(rf_viewsets.ModelViewSet):
    queryset = models.Section.objects.all().order_by('title')
    lookup_field = 'key'
    serializer_class = serializers.SectionSerializer
    filter_backends = (DjangoFilterBackend,)
    permission_classes = [rf_permissions.IsAuthenticated, core_permissions.IsStaff]
