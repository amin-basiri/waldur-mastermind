import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, signals
from django.utils.timezone import now

from waldur_core.core import utils as core_utils
from waldur_core.structure import models as structure_models
from waldur_core.structure.log import event_logger
from waldur_core.structure.models import Customer, CustomerRole, Project
from waldur_mastermind.marketplace_script import PLUGIN_NAME as SCRIPT_PLUGIN_NAME
from waldur_mastermind.marketplace_slurm_remote import (
    PLUGIN_NAME as SLURM_REMOTE_PLUGIN_NAME,
)

from . import PLUGIN_NAME, callbacks, log, models, tasks, utils

logger = logging.getLogger(__name__)

OFFERING_USER_ALLOWED_OFFERING_TYPES = [
    PLUGIN_NAME,
    SLURM_REMOTE_PLUGIN_NAME,
    SCRIPT_PLUGIN_NAME,
]


def create_screenshot_thumbnail(sender, instance, created=False, **kwargs):
    if not created:
        return

    transaction.on_commit(
        lambda: tasks.create_screenshot_thumbnail.delay(instance.uuid)
    )


def log_order_events(sender, instance, created=False, **kwargs):
    order = instance
    if created:
        # Skip logging for imported orders
        if order.state != models.Order.States.DONE:
            log.log_order_created(order)
    elif not order.tracker.has_changed('state'):
        return
    elif order.state == models.Order.States.EXECUTING:
        log.log_order_approved(order)
    elif order.state == models.Order.States.REJECTED:
        log.log_order_rejected(order)
    elif order.state == models.Order.States.DONE:
        log.log_order_completed(order)
    elif order.state == models.Order.States.TERMINATED:
        log.log_order_terminated(order)
    elif order.state == models.Order.States.ERRED:
        log.log_order_failed(order)


def log_order_item_events(sender, instance, created=False, **kwargs):
    order_item = instance
    if not created:
        return
    if order_item.state != models.OrderItem.States.PENDING:
        # Skip logging for imported order item
        return
    elif not order_item.resource:
        return
    elif order_item.type == models.OrderItem.Types.TERMINATE:
        log.log_resource_terminate_requested(order_item.resource)
    elif order_item.type == models.OrderItem.Types.UPDATE:
        log.log_resource_update_requested(order_item.resource)


def log_resource_events(sender, instance, created=False, **kwargs):
    resource = instance
    # Skip logging for imported resource
    if created and instance.state == models.Resource.States.CREATING:
        log.log_resource_creation_requested(resource)


def complete_order_when_all_items_are_done(sender, instance, created=False, **kwargs):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state not in models.OrderItem.States.TERMINAL_STATES:
        return

    if instance.order.state != models.Order.States.EXECUTING:
        return

    order = instance.order
    # check if there are any non-finished OrderItems left and finish order if none is found
    if (
        models.OrderItem.objects.filter(order=order)
        .exclude(state__in=models.OrderItem.States.TERMINAL_STATES)
        .exists()
    ):
        return

    order.complete()
    order.save(update_fields=['state'])


def update_category_quota_when_offering_is_created(
    sender, instance, created=False, **kwargs
):
    def get_delta():
        if created:
            if instance.state == models.Offering.States.ACTIVE:
                return 1
        else:
            if instance.tracker.has_changed('state'):
                if instance.state == models.Offering.States.ACTIVE:
                    return 1
                elif (
                    instance.tracker.previous('state') == models.Offering.States.ACTIVE
                ):
                    return -1

    delta = get_delta()
    if delta:
        instance.category.add_quota_usage(models.Category.Quotas.offering_count, delta)


def update_category_quota_when_offering_is_deleted(sender, instance, **kwargs):
    if instance.state == models.Offering.States.ACTIVE:
        instance.category.add_quota_usage(models.Category.Quotas.offering_count, -1)


def update_category_offerings_count(sender, **kwargs):
    for category in models.Category.objects.all():
        value = models.Offering.objects.filter(
            category=category, state=models.Offering.States.ACTIVE
        ).count()
        category.set_quota_usage(models.Category.Quotas.offering_count, value)


def update_aggregate_resources_count_when_resource_is_updated(
    sender, instance, created=False, **kwargs
):
    def apply_change(delta):
        for field in ('project', 'customer'):
            try:
                scope = getattr(instance, field)
            except ObjectDoesNotExist:
                # When project is deleted, all its resources are deleted via cascade.
                # Therefore it is okay if project does not exists.
                continue
            counter, _ = models.AggregateResourceCount.objects.get_or_create(
                scope=scope,
                category=instance.offering.category,
            )
            if delta == 1:
                counter.count += 1
            elif delta == -1:
                counter.count = max(0, counter.count - 1)

            counter.save(update_fields=['count'])

    if created and instance.state != models.Resource.States.TERMINATED:
        apply_change(1)
    elif (
        instance.tracker.has_changed('state')
        and instance.state == models.Resource.States.TERMINATED
    ):
        apply_change(-1)


def update_aggregate_resources_count(sender, **kwargs):
    models.AggregateResourceCount.objects.update(count=0)
    for category in models.Category.objects.all():
        for field, content_type in (
            ('project_id', ContentType.objects.get_for_model(Project)),
            ('project__customer_id', ContentType.objects.get_for_model(Customer)),
        ):
            rows = (
                models.Resource.objects.filter(offering__category=category)
                .order_by()
                .exclude(state=models.Resource.States.TERMINATED)
                .values(field, 'offering__category')
                .annotate(count=Count('*'))
            )
            for row in rows:
                models.AggregateResourceCount.objects.update_or_create(
                    content_type=content_type,
                    object_id=row[field],
                    category=category,
                    defaults={'count': row['count']},
                )


def close_resource_plan_period_when_resource_is_terminated(
    sender, instance, created=False, **kwargs
):
    """
    Handle case when resource has been terminated by service provider.
    """

    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state != models.Resource.States.TERMINATED:
        return

    if instance.tracker.previous('state') == models.Resource.States.TERMINATING:
        # It is expected that this case is handled using callbacks
        return

    if not instance.plan:
        return

    models.ResourcePlanPeriod.objects.filter(
        resource=instance, plan=instance.plan, end=None
    ).update(end=now())


def reject_order(sender, instance, created=False, **kwargs):
    if created:
        return

    order = instance

    if not order.tracker.has_changed('state'):
        return

    if (
        instance.tracker.previous('state') == models.Order.States.REQUESTED_FOR_APPROVAL
        and order.state == models.Order.States.REJECTED
    ):
        for item in order.items.all():
            item.set_state_terminated(termination_comment="Order rejected")
            item.save(update_fields=['state'])


def change_order_item_state(sender, instance, created=False, **kwargs):
    if created or not instance.tracker.has_changed('state'):
        return

    try:
        resource = models.Resource.objects.get(scope=instance)
    except ObjectDoesNotExist:
        logger.warning(
            'Skipping resource state synchronization '
            'because marketplace resource is not found. '
            'Resource ID: %s',
            core_utils.serialize_instance(instance),
        )
    else:
        callbacks.sync_resource_state(instance, resource)


def terminate_resource(sender, instance, **kwargs):
    try:
        resource = models.Resource.objects.get(scope=instance)
    except ObjectDoesNotExist:
        logger.debug(
            'Skipping terminate for resource '
            'because marketplace resource does not exist. '
            'Resource ID: %s',
            core_utils.serialize_instance(instance),
        )
    else:
        callbacks.resource_deletion_succeeded(resource)


def connect_resource_handlers(*resources):
    for index, model in enumerate(resources):
        suffix = f'{index}_{model.__class__}'

        signals.post_save.connect(
            change_order_item_state,
            sender=model,
            dispatch_uid='waldur_mastermind.marketplace.change_order_item_state_%s'
            % suffix,
        )

        signals.pre_delete.connect(
            terminate_resource,
            sender=model,
            dispatch_uid='waldur_mastermind.marketplace.terminate_resource_%s' % suffix,
        )


def synchronize_resource_metadata(sender, instance, created=False, **kwargs):
    fields = {
        'action',
        'action_details',
        'state',
        'runtime_state',
        'name',
        'backend_id',
    }
    if not created and not set(instance.tracker.changed()) & fields:
        return

    try:
        resource = models.Resource.objects.get(scope=instance)
    except ObjectDoesNotExist:
        logger.debug(
            'Skipping resource synchronization for OpenStack resource '
            'because marketplace resource does not exist. '
            'Resource ID: %s',
            instance.id,
        )
        return

    utils.import_resource_metadata(resource)


def connect_resource_metadata_handlers(*resources):
    for index, model in enumerate(resources):
        signals.post_save.connect(
            synchronize_resource_metadata,
            sender=model,
            dispatch_uid='waldur_mastermind.marketplace.'
            'synchronize_resource_metadata_{}_{}'.format(index, model.__class__),
        )


def update_or_create_quotas(resource):
    components_map = resource.offering.get_limit_components()
    for key, value in resource.limits.items():
        component = components_map.get(key)
        if component:
            models.ComponentQuota.objects.update_or_create(
                resource=resource, component=component, defaults={'limit': value}
            )


def sync_limits(sender, instance, created=False, **kwargs):
    if not created and not instance.tracker.has_changed('limits'):
        return
    transaction.on_commit(lambda: update_or_create_quotas(instance))


@transaction.atomic()
def limit_update_succeeded(sender, order_item, **kwargs):
    resource = order_item.resource
    old_limits = resource.limits
    resource.limits = order_item.limits
    resource.save()
    order_item.set_state_done()
    order_item.save(update_fields=['state'])
    logger.info(
        'Resource limits have been updated. Resource: %s, old limits: %s, new limits: %s, created by: %s',
        core_utils.serialize_instance(resource),
        old_limits,
        resource.limits,
        order_item.order.created_by,
    )
    log.log_resource_limit_update_succeeded(resource)


def limit_update_failed(sender, order_item, error_message, **kwargs):
    order_item.set_state_erred()
    order_item.error_message = error_message
    order_item.save()
    resource = order_item.resource
    logger.info(
        'Resource limit update failed. Resource: %s, requested limits: %s, created by: %s, '
        'error message: %s',
        core_utils.serialize_instance(resource),
        resource.limits,
        order_item.order.created_by,
        error_message,
    )
    log.log_resource_limit_update_failed(resource)


def log_offering_permission_granted(
    sender, structure, user, role=None, created_by=None, **kwargs
):
    log.log_offering_permission_granted(structure, user, created_by)


def log_offering_permission_revoked(
    sender, structure, user, role=None, removed_by=None, **kwargs
):
    log.log_offering_permission_revoked(structure, user, removed_by)


def log_offering_permission_updated(sender, instance, user, **kwargs):
    log.log_offering_permission_updated(instance, user)


def add_service_manager_role_to_customer(
    sender, structure, user, role=None, created_by=None, **kwargs
):
    if not structure.customer.has_user(user, CustomerRole.SERVICE_MANAGER):
        structure.customer.add_user(user, CustomerRole.SERVICE_MANAGER)


def drop_service_manager_role_from_customer(
    sender, structure, user, role=None, removed_by=None, **kwargs
):
    if not models.OfferingPermission.objects.filter(
        offering__customer=structure.customer, user=user, is_active=True
    ).exists():
        structure.customer.remove_user(user, CustomerRole.SERVICE_MANAGER)


def drop_offering_permissions_if_service_manager_role_is_revoked(
    sender, structure, user, role=None, removed_by=None, **kwargs
):
    if role != CustomerRole.SERVICE_MANAGER:
        return
    for perm in models.OfferingPermission.objects.filter(
        offering__customer=structure, user=user, is_active=True
    ):
        perm.revoke()


def update_customer_of_offering_if_project_has_been_moved(
    sender, project, old_customer, new_customer, **kwargs
):
    models.Offering.objects.filter(project=project, customer=old_customer).update(
        customer=new_customer
    )


def disable_empty_service_settings(offering):
    service_settings = getattr(offering, 'scope', None)
    if not service_settings:
        return

    if not isinstance(service_settings, structure_models.ServiceSettings):
        return

    if (
        not models.Resource.objects.filter(offering=offering)
        .exclude(state=models.Resource.States.TERMINATED)
        .exists()
    ):
        service_settings.is_active = False
        service_settings.save(update_fields=['is_active'])


def enable_nonempty_service_settings(offering):
    service_settings = getattr(offering, 'scope', None)
    if not service_settings:
        return

    if not isinstance(service_settings, structure_models.ServiceSettings):
        return

    if (
        models.Resource.objects.filter(offering=offering)
        .exclude(state=models.Resource.States.TERMINATED)
        .exists()
    ):
        service_settings.is_active = True
        service_settings.save(update_fields=['is_active'])


def disable_archived_service_settings_without_existing_resource(
    sender, instance, created=False, **kwargs
):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state != models.Resource.States.TERMINATED:
        return

    offering: models.Offering = instance.offering

    if offering.state != models.Offering.States.ARCHIVED:
        return

    disable_empty_service_settings(offering)


def disable_service_settings_without_existing_resource_when_archived(
    sender, instance, created=False, **kwargs
):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state != models.Offering.States.ARCHIVED:
        return

    disable_empty_service_settings(instance)


def enable_service_settings_with_existing_resource(
    sender, instance, created=False, **kwargs
):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state in [
        models.Resource.States.TERMINATED,
        models.Resource.States.TERMINATING,
    ]:
        return

    enable_nonempty_service_settings(instance.offering)


def enable_service_settings_when_not_archived(
    sender, instance, created=False, **kwargs
):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state == models.Offering.States.ARCHIVED:
        return

    enable_nonempty_service_settings(instance)


def resource_has_been_renamed(sender, instance, created=False, **kwargs):
    if created:
        return

    if not instance.tracker.has_changed('name'):
        return

    log.log_marketplace_resource_renamed(
        instance, instance.tracker.previous('name') or ''
    )


def delete_expired_project_if_every_resource_has_been_terminated(
    sender, instance, created=False, **kwargs
):
    if created:
        return

    if not instance.tracker.has_changed('state'):
        return

    if instance.state != models.Resource.States.TERMINATED:
        return

    project = instance.project

    if project.is_expired:
        resources = (
            models.Resource.objects.filter(project=project)
            .exclude(
                state__in=(
                    models.Resource.States.ERRED,
                    models.Resource.States.TERMINATED,
                )
            )
            .exists()
        )
        if not resources:
            event_logger.project.info(
                'Project {project_name} is going to be deleted because end date has been reached and there are no active resources.',
                event_type='project_deletion_triggered',
                event_context={'project': project},
            )
            project.delete()


def log_offering_user_created(sender, instance, created=False, **kwargs):
    if not created:
        return
    log.log_offering_user_created(instance)


def log_offering_user_deleted(sender, instance, **kwargs):
    log.log_offering_user_deleted(instance)


def generate_changes_string(changed_dict, instance):
    changes_string = ""
    if 'username' in changed_dict:
        changes_string += f"Robot account {changed_dict['username']} has been updated. "
    else:
        changes_string += f"Robot account {instance.username} has been updated. "

    for key in changed_dict:
        change_string = (
            f"{key} had changed from {changed_dict[key]} to {getattr(instance, key)}. "
        )
        changes_string += change_string
    return changes_string


def log_resource_robot_account_created_or_updated(
    sender, instance, created=False, **kwargs
):
    if not created:
        changed_string = generate_changes_string(instance.tracker.changed(), instance)
        event_logger.marketplace_robot_account.info(
            changed_string,
            event_type='resource_robot_account_updated',
            event_context={'robot_account': instance},
        )
        return
    event_logger.marketplace_robot_account.info(
        'Robot account {robot_account_username} has been created.',
        event_type='resource_robot_account_created',
        event_context={'robot_account': instance},
    )


def log_resource_robot_account_deleted(sender, instance, **kwargs):
    event_logger.marketplace_robot_account.info(
        'Robot account {robot_account_username} has been deleted.',
        event_type='resource_robot_account_deleted',
        event_context={'robot_account': instance},
    )


def create_offering_users_when_project_role_granted(sender, structure, user, **kwargs):
    project = structure
    resources = project.resource_set.filter(
        state=models.Resource.States.OK,
        offering__type__in=OFFERING_USER_ALLOWED_OFFERING_TYPES,
    )
    offering_ids = set(resources.values_list('offering_id', flat=True))
    offerings = models.Offering.objects.filter(id__in=offering_ids)

    for offering in offerings:
        if not offering.secret_options.get('service_provider_can_create_offering_user'):
            logger.info(
                'It is not allowed to create users for current offering %s.', offering
            )
            continue

        if models.OfferingUser.objects.filter(
            offering=offering,
            user=user,
        ).exists():
            logger.info('An offering user for %s in %s already exists', user, offering)
            continue

        username = utils.generate_username(user, offering)

        offering_user = models.OfferingUser.objects.create(
            offering=offering,
            user=user,
            username=username,
        )
        utils.setup_linux_related_data(offering_user, offering)
        offering_user.save(update_fields=['backend_metadata'])


def create_offering_user_for_new_resource(sender, instance, **kwargs):
    resource = instance
    project = resource.project
    users = project.get_users()
    offering = resource.offering
    if offering.type not in OFFERING_USER_ALLOWED_OFFERING_TYPES:
        logger.info(
            'The offering %s does not support offering users feature.', offering
        )
        return

    if not offering.secret_options.get('service_provider_can_create_offering_user'):
        logger.info(
            'It is not allowed to create users for current offering %s.', offering
        )
        return

    for user in users:
        if models.OfferingUser.objects.filter(
            offering=offering,
            user=user,
        ).exists():
            logger.info('An offering user for %s in %s already exists', user, offering)
            continue

        username = utils.generate_username(user, offering)

        offering_user = models.OfferingUser.objects.create(
            offering=offering,
            user=user,
            username=username,
        )

        offering_user.set_propagation_date()

        utils.setup_linux_related_data(offering_user, offering)
        offering_user.save(update_fields=['propagation_date', 'backend_metadata'])

        logger.info('The offering user %s has been created', offering_user)


def update_offering_user_username_after_offering_settings_change(
    sender, instance, created=False, **kwargs
):
    if created:
        return

    offering = instance

    if (
        offering.type not in OFFERING_USER_ALLOWED_OFFERING_TYPES
        or not offering.tracker.has_changed('plugin_options')
    ):
        return

    offering_users = models.OfferingUser.objects.filter(offering=offering)

    for offering_user in offering_users:
        new_username = utils.generate_username(offering_user.user, offering)
        logger.info('New username for %s is %s', offering_user, new_username)
        offering_user.username = new_username

        utils.setup_linux_related_data(offering_user, offering)
        offering_user.save(update_fields=['username', 'backend_metadata'])
