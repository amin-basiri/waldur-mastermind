import logging

from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import transaction
from rest_framework import exceptions

from waldur_core.core.utils import serialize_instance
from waldur_core.structure import ServiceBackend
from waldur_core.structure import models as structure_models
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace import plugins
from waldur_mastermind.marketplace.utils import (
    format_list,
    get_resource_state,
    import_resource_metadata,
)
from waldur_mastermind.marketplace_openstack import (
    CORES_TYPE,
    INSTANCE_TYPE,
    PACKAGE_TYPE,
    RAM_TYPE,
    STORAGE_MODE_DYNAMIC,
    STORAGE_MODE_FIXED,
    STORAGE_TYPE,
    VOLUME_TYPE,
)
from waldur_mastermind.packages import models as package_models
from waldur_mastermind.packages.serializers import _apply_quotas
from waldur_openstack.openstack import apps as openstack_apps
from waldur_openstack.openstack import models as openstack_models
from waldur_openstack.openstack_tenant import apps as openstack_tenant_apps
from waldur_openstack.openstack_tenant import models as openstack_tenant_models

logger = logging.getLogger(__name__)
TenantQuotas = openstack_models.Tenant.Quotas


def get_offering_category_for_tenant():
    return marketplace_models.Category.objects.get(
        uuid=settings.WALDUR_MARKETPLACE_OPENSTACK['TENANT_CATEGORY_UUID']
    )


def get_offering_name_for_instance(tenant):
    return 'Virtual machine in %s' % tenant.name


def get_offering_category_for_instance():
    return marketplace_models.Category.objects.get(
        uuid=settings.WALDUR_MARKETPLACE_OPENSTACK['INSTANCE_CATEGORY_UUID']
    )


def get_offering_name_for_volume(tenant):
    return 'Volume in %s' % tenant.name


def get_offering_category_for_volume():
    return marketplace_models.Category.objects.get(
        uuid=settings.WALDUR_MARKETPLACE_OPENSTACK['VOLUME_CATEGORY_UUID']
    )


def get_category_and_name_for_offering_type(offering_type, service_settings):
    if offering_type == INSTANCE_TYPE:
        category = get_offering_category_for_instance()
        name = get_offering_name_for_instance(service_settings)
        return category, name
    elif offering_type == VOLUME_TYPE:
        category = get_offering_category_for_volume()
        name = get_offering_name_for_volume(service_settings)
        return category, name


def create_offering_components(offering):
    fixed_components = plugins.manager.get_components(PACKAGE_TYPE)

    for component_data in fixed_components:
        marketplace_models.OfferingComponent.objects.create(
            offering=offering, **component_data._asdict()
        )


def copy_plan_components_from_template(plan, offering, template):
    component_map = {
        component.type: component for component in template.components.all()
    }

    for (key, component_data) in component_map.items():
        plan_component = component_map.get(key)
        offering_component = offering.components.get(type=key)

        amount = plan_component.amount
        price = plan_component.price

        # In marketplace RAM and storage is stored in GB, but in package plugin it is stored in MB.
        if key in (RAM_TYPE, STORAGE_TYPE):
            amount = int(amount / 1024)
            price = price * 1024

        marketplace_models.PlanComponent.objects.create(
            plan=plan, component=offering_component, amount=amount, price=price,
        )


def import_openstack_service_settings(
    default_customer, dry_run=False, require_templates=False
):
    """
    Import OpenStack service settings as marketplace offerings.
    """
    service_type = openstack_apps.OpenStackConfig.service_name
    category = get_offering_category_for_tenant()

    package_offerings = marketplace_models.Offering.objects.filter(type=PACKAGE_TYPE)
    front_settings = set(
        package_offerings.exclude(object_id=None).values_list('object_id', flat=True)
    )

    back_settings = structure_models.ServiceSettings.objects.filter(type=service_type)
    missing_settings = back_settings.exclude(id__in=front_settings)

    if dry_run:
        logger.warning(
            'OpenStack service settings would be imported to marketplace. ' 'ID: %s.',
            format_list(missing_settings),
        )
        return 0, 0

    missing_templates = package_models.PackageTemplate.objects.filter(
        service_settings__in=missing_settings
    )

    settings_without_templates = missing_settings.exclude(
        id__in=missing_templates.values_list('service_settings_id', flat=True)
    )

    def create_offering(service_settings, state):
        offering = marketplace_models.Offering.objects.create(
            scope=service_settings,
            type=PACKAGE_TYPE,
            name=service_settings.name,
            geolocations=service_settings.geolocations,
            customer=service_settings.customer or default_customer,
            category=category,
            shared=service_settings.shared,
            state=state,
        )
        create_offering_components(offering)
        return offering

    offerings_counter = 0
    plans_counter = 0

    if settings_without_templates.exists():
        logger.warning(
            'The following service settings do not have package template, '
            'therefore they would be imported in DRAFT state: %s',
            format_list(settings_without_templates),
        )

    if not require_templates:
        for service_settings in settings_without_templates:
            with transaction.atomic():
                create_offering(
                    service_settings, marketplace_models.Offering.States.DRAFT
                )
                offerings_counter += 1

    for template in missing_templates:
        with transaction.atomic():
            service_settings = template.service_settings

            try:
                offering = marketplace_models.Offering.objects.get(
                    scope=service_settings
                )
            except marketplace_models.Offering.DoesNotExist:
                offering = create_offering(
                    service_settings, marketplace_models.Offering.States.ACTIVE
                )
                offerings_counter += 1

            plan = marketplace_models.Plan.objects.create(
                offering=offering,
                name=template.name,
                unit_price=template.price,
                unit=marketplace_models.Plan.Units.PER_DAY,
                product_code=template.product_code,
                article_code=template.article_code,
                scope=template,
            )
            plans_counter += 1

            copy_plan_components_from_template(plan, offering, template)

    return offerings_counter, plans_counter


def import_openstack_tenants(dry_run=False):
    """
    Import OpenStack tenants as marketplace resources.
    It is expected that offerings for OpenStack service settings are imported before this command is ran.
    """
    front_ids = set(
        marketplace_models.Resource.objects.filter(
            offering__type=PACKAGE_TYPE
        ).values_list('object_id', flat=True)
    )
    missing_resources = openstack_models.Tenant.objects.exclude(id__in=front_ids)

    if dry_run:
        logger.warning(
            'OpenStack tenants would be imported to marketplace. ' 'ID: %s.',
            format_list(missing_resources),
        )
        return 0

    packages = package_models.OpenStackPackage.objects.filter(
        tenant__in=missing_resources
    )
    tenants_without_packages = missing_resources.exclude(
        id__in=packages.values_list('tenant_id', flat=True)
    )

    def create_resource(offering, tenant, plan=None):
        resource = marketplace_models.Resource.objects.create(
            name=tenant.name,
            created=tenant.created,
            offering=offering,
            plan=plan,
            scope=tenant,
            project=tenant.project,
            state=get_resource_state(tenant.state),
            attributes=dict(
                name=tenant.name,
                description=tenant.description,
                user_username=tenant.user_username,
                user_password=tenant.user_password,
            ),
        )
        if plan and tenant.backend_id:
            marketplace_models.ResourcePlanPeriod.objects.create(
                resource=resource, plan=plan, start=tenant.created,
            )
        import_resource_metadata(resource)
        return resource

    resource_counter = 0
    for tenant in tenants_without_packages:
        # It is expected that service setting has exactly one offering
        # if it does not have package
        try:
            offering = marketplace_models.Offering.objects.get(
                scope=tenant.service_settings
            )
        except marketplace_models.Offering.DoesNotExist:
            logger.warning(
                'Offering for service setting is not imported yet. '
                'Service setting ID: %s.',
                tenant.service_settings.id,
            )
            continue

        create_resource(offering, tenant)
        resource_counter += 1

    for package in packages:
        tenant = package.tenant
        try:
            offering = marketplace_models.Offering.objects.get(
                scope=tenant.service_settings
            )
            plan = marketplace_models.Plan.objects.get(
                scope=package.template, offering=offering
            )
        except marketplace_models.Plan.DoesNotExist:
            logger.warning(
                'Plan for template is not imported yet. ' 'Template ID: %s.',
                package.template_id,
            )
            continue

        create_resource(plan.offering, tenant, plan)
        resource_counter += 1

    return resource_counter


def import_openstack_tenant_service_settings(dry_run=False):
    """
    Import OpenStack tenant service settings as marketplace offerings.
    """

    offerings_counter = 0
    plans_counter = 0

    for offering_type in (INSTANCE_TYPE, VOLUME_TYPE):
        marketplace_offerings = marketplace_models.Offering.objects.filter(
            type=offering_type
        )
        front_settings = set(
            marketplace_offerings.exclude(object_id=None).values_list(
                'object_id', flat=True
            )
        )
        missing_settings = structure_models.ServiceSettings.objects.filter(
            type=openstack_tenant_apps.OpenStackTenantConfig.service_name
        ).exclude(id__in=front_settings)

        if dry_run:
            logger.warning(
                'OpenStack tenant service settings would be imported to marketplace. '
                'ID: %s.',
                format_list(missing_settings),
            )
            continue

        packages = package_models.OpenStackPackage.objects.filter(
            service_settings__in=missing_settings
        )
        settings_to_template = {
            package.service_settings: package.template for package in packages
        }

        for service_settings in missing_settings:
            category, offering_name = get_category_and_name_for_offering_type(
                offering_type, service_settings
            )
            offering = marketplace_models.Offering.objects.create(
                customer=service_settings.customer,
                category=category,
                name=offering_name,
                scope=service_settings,
                shared=service_settings.shared,
                type=offering_type,
                state=marketplace_models.Offering.States.ACTIVE,
                billable=False,
            )
            create_offering_components(offering)
            offerings_counter += 1

            template = settings_to_template.get(service_settings)
            if not template:
                logger.warning(
                    'Billing for service setting is not imported because it does not have template. '
                    'Service setting ID: %s',
                    service_settings.id,
                )
                continue

            try:
                parent_plan = marketplace_models.Plan.objects.get(
                    scope=template, offering__type=PACKAGE_TYPE
                )
            except marketplace_models.Plan.DoesNotExist:
                logger.warning(
                    'Billing for template is not imported because it does not have plan. '
                    'Template ID: %s',
                    template.id,
                )
                continue

            plan = marketplace_models.Plan.objects.create(
                offering=offering, name=parent_plan.name, scope=parent_plan.scope
            )

            copy_plan_components_from_template(plan, offering, template)
            plans_counter += 1

    return offerings_counter, plans_counter


def get_plan_for_resource(resource, offering):
    tenant = resource.service_settings.scope
    if not tenant:
        logger.warning(
            'Skipping billing for resource because it does not have shared OpenStack settings. '
            'Resource: %s',
            serialize_instance(resource),
        )
        return

    try:
        package = package_models.OpenStackPackage.objects.get(tenant=tenant)
    except package_models.OpenStackPackage.DoesNotExist:
        logger.warning(
            'Skipping billing for resource because package for tenant is not defined. '
            'Tenant ID: %s',
            tenant.id,
        )
        return

    try:
        plan = marketplace_models.Plan.objects.get(
            scope=package.template, offering=offering
        )
    except marketplace_models.Plan.DoesNotExist:
        logger.warning(
            'Skipping billing for resource because plan for template is not defined. '
            'Template ID: %s',
            package.template,
        )
        return

    return plan


def import_openstack_instances_and_volumes(dry_run=False):
    """
    Import OpenStack tenant resources as marketplace resources.
    It is expected that offerings for OpenStack tenant service settings are imported before this command is ran.
    """
    model_classes = {
        INSTANCE_TYPE: openstack_tenant_models.Instance,
        VOLUME_TYPE: openstack_tenant_models.Volume,
    }

    resources_counter = 0

    for offering_type in (INSTANCE_TYPE, VOLUME_TYPE):
        front_ids = set(
            marketplace_models.Resource.objects.filter(
                offering__type=offering_type
            ).values_list('object_id', flat=True)
        )

        model_class = model_classes[offering_type]
        missing_resources = model_class.objects.exclude(id__in=front_ids)

        if dry_run:
            ids = format_list(missing_resources)
            logger.warning(
                'OpenStack resource with IDs would be imported to marketplace: %s.', ids
            )
            continue

        offerings = {
            offering.scope: offering
            for offering in marketplace_models.Offering.objects.filter(
                type=offering_type
            )
        }

        for resource in missing_resources:
            offering = offerings.get(resource.service_settings)
            if not offering:
                logger.warning(
                    'Offering for service setting with ID %s is not imported yet.',
                    resource.service_settings.id,
                )
                continue

            plan = get_plan_for_resource(resource, offering)

            new_resource = marketplace_models.Resource.objects.create(
                name=resource.name,
                created=resource.created,
                project=resource.project,
                offering=offering,
                plan=plan,
                scope=resource,
                state=get_resource_state(resource.state),
                attributes=dict(name=resource.name, description=resource.description,),
            )
            if isinstance(resource, openstack_tenant_models.Volume):
                import_volume_metadata(new_resource)
            if isinstance(resource, openstack_tenant_models.Instance):
                import_instance_metadata(new_resource)
            resources_counter += 1

    return resources_counter


def import_volume_metadata(resource):
    import_resource_metadata(resource)
    volume = resource.scope
    resource.backend_metadata['size'] = volume.size

    if volume.instance:
        resource.backend_metadata['instance_uuid'] = volume.instance.uuid.hex
        resource.backend_metadata['instance_name'] = volume.instance.name
    else:
        resource.backend_metadata['instance_uuid'] = None
        resource.backend_metadata['instance_name'] = None

    if volume.type:
        resource.backend_metadata['type_name'] = volume.type.name
    else:
        resource.backend_metadata['type_name'] = None

    resource.save(update_fields=['backend_metadata'])


def import_instance_metadata(resource):
    import_resource_metadata(resource)
    instance = resource.scope
    resource.backend_metadata['internal_ips'] = instance.internal_ips
    resource.backend_metadata['external_ips'] = instance.external_ips
    resource.save(update_fields=['backend_metadata'])


def get_offering(offering_type, service_settings):
    try:
        return marketplace_models.Offering.objects.get(
            scope=service_settings, type=offering_type
        )
    except ObjectDoesNotExist:
        logger.warning(
            'Marketplace offering is not found. ' 'ServiceSettings ID: %s',
            service_settings.id,
        )
    except MultipleObjectsReturned:
        logger.warning(
            'Multiple marketplace offerings are found. ' 'ServiceSettings ID: %s',
            service_settings.id,
        )


def import_quotas(offering, quotas, field):
    source_values = {row['name']: row[field] for row in quotas.values('name', field)}
    storage_mode = offering.plugin_options.get('storage_mode') or STORAGE_MODE_FIXED

    result_values = {
        CORES_TYPE: source_values.get(TenantQuotas.vcpu.name, 0),
        RAM_TYPE: source_values.get(TenantQuotas.ram.name, 0),
    }

    if storage_mode == STORAGE_MODE_FIXED:
        result_values[STORAGE_TYPE] = source_values.get(TenantQuotas.storage.name, 0)
    elif storage_mode == STORAGE_MODE_DYNAMIC:
        volume_type_values = {
            k: v for (k, v) in source_values.items() if k.startswith('gigabytes_')
        }
        result_values.update(volume_type_values)

    return result_values


def import_usage(resource):
    tenant = resource.scope

    if not tenant:
        return

    resource.current_usages = import_quotas(resource.offering, tenant.quotas, 'usage')
    resource.save(update_fields=['current_usages'])


def import_limits(resource):
    """
    Import resource quotas as marketplace limits.
    :param resource: Marketplace resource
    """
    tenant = resource.scope

    if not tenant:
        return

    resource.limits = import_quotas(resource.offering, tenant.quotas, 'limit')
    resource.save(update_fields=['limits'])


def map_limits_to_quotas(limits):
    quotas = {
        TenantQuotas.vcpu.name: limits.get(CORES_TYPE),
        TenantQuotas.ram.name: limits.get(RAM_TYPE),
        TenantQuotas.storage.name: limits.get(STORAGE_TYPE),
    }

    quotas = {k: v for k, v in quotas.items() if v is not None}

    # Filter volume-type quotas.
    volume_type_quotas = dict(
        (key, value)
        for (key, value) in limits.items()
        if key.startswith('gigabytes_') and value is not None
    )

    # Common storage quota should be equal to sum of all volume-type quotas.
    if volume_type_quotas:
        if 'storage' in quotas:
            raise exceptions.ValidationError(
                'You should either specify general-purpose storage quota '
                'or volume-type specific storage quota.'
            )
        quotas['storage'] = ServiceBackend.gb2mb(sum(list(volume_type_quotas.values())))
        quotas.update(volume_type_quotas)

    return quotas


def update_limits(order_item):
    tenant = order_item.resource.scope
    backend = tenant.get_backend()
    quotas = map_limits_to_quotas(order_item.limits)
    backend.push_tenant_quotas(tenant, quotas)
    with transaction.atomic():
        _apply_quotas(tenant, quotas)
        for target in structure_models.ServiceSettings.objects.filter(scope=tenant):
            _apply_quotas(target, quotas)


def merge_plans(offering, example_plan):
    new_plan = marketplace_models.Plan.objects.create(
        offering=offering,
        name='Default',
        unit=example_plan.unit,
        unit_price=0,  # there are no fixed components thus price is zero
        product_code=example_plan.product_code,
        article_code=example_plan.article_code,
    )
    for component in example_plan.components.all():
        marketplace_models.PlanComponent.objects.create(
            plan=new_plan, component=component.component, price=component.price,
        )
    marketplace_models.Resource.objects.filter(offering=offering).update(plan=new_plan)
    marketplace_models.ResourcePlanPeriod.objects.filter(
        plan__offering=offering
    ).update(plan=new_plan)
    marketplace_models.OrderItem.objects.filter(plan__offering=offering).update(
        plan=new_plan
    )
    marketplace_models.OrderItem.objects.filter(old_plan__offering=offering).update(
        old_plan=new_plan
    )
    offering.plans.exclude(pk=new_plan.pk).delete()


def import_limits_when_storage_mode_is_switched(resource):
    tenant = resource.scope

    if not tenant:
        return

    storage_mode = (
        resource.offering.plugin_options.get('storage_mode') or STORAGE_MODE_FIXED
    )

    raw_limits = {quota.name: quota.limit for quota in tenant.quotas.all()}
    raw_usages = {quota.name: quota.usage for quota in tenant.quotas.all()}

    limits = {
        CORES_TYPE: raw_limits.get(TenantQuotas.vcpu.name, 0),
        RAM_TYPE: raw_limits.get(TenantQuotas.ram.name, 0),
    }

    if storage_mode == STORAGE_MODE_FIXED:
        limits[STORAGE_TYPE] = raw_usages.get(TenantQuotas.storage.name, 0)
    elif storage_mode == STORAGE_MODE_DYNAMIC:
        volume_type_limits = {
            k: v for (k, v) in raw_usages.items() if k.startswith('gigabytes_')
        }
        limits.update(volume_type_limits)

    resource.limits = limits
    resource.save(update_fields=['limits'])


def push_tenant_limits(resource):
    tenant = resource.scope
    backend = tenant.get_backend()
    quotas = map_limits_to_quotas(resource.limits)
    backend.push_tenant_quotas(tenant, quotas)
    with transaction.atomic():
        _apply_quotas(tenant, quotas)
        for target in structure_models.ServiceSettings.objects.filter(scope=tenant):
            _apply_quotas(target, quotas)
