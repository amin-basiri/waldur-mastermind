import io
import logging
from collections import defaultdict

import requests
import urllib3
from django.utils import dateparse
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError
from waldur_client import WaldurClient, WaldurClientException

from waldur_core.core.utils import get_system_robot
from waldur_core.structure import models as structure_models
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace_remote.constants import (
    OFFERING_COMPONENT_FIELDS,
    PLAN_FIELDS,
)

from . import PLUGIN_NAME

logger = logging.getLogger(__name__)

INVALID_RESOURCE_STATES = (
    marketplace_models.Resource.States.CREATING,
    marketplace_models.Resource.States.TERMINATED,
)


def get_client_for_offering(offering):
    options = offering.secret_options
    api_url = options['api_url']
    token = options['token']
    return WaldurClient(api_url, token)


def get_project_backend_id(project):
    return f'{project.customer.uuid}_{project.uuid}'


def pull_fields(fields, local_object, remote_object):
    changed_fields = set()
    for field in fields:
        if remote_object[field] != getattr(local_object, field):
            setattr(local_object, field, remote_object[field])
            changed_fields.add(field)
    if changed_fields:
        local_object.save(update_fields=changed_fields)
    return changed_fields


def get_remote_offerings_for_project(project):
    offering_ids = (
        marketplace_models.Resource.objects.filter(
            project=project,
            offering__type=PLUGIN_NAME,
            offering__state=marketplace_models.Offering.States.ACTIVE,
        )
        .exclude(state__in=INVALID_RESOURCE_STATES)
        .values_list('offering', flat=True)
        .distinct()
    )
    return marketplace_models.Offering.objects.filter(pk__in=offering_ids)


def get_projects_with_remote_offerings():
    projects_with_offerings = defaultdict(set)
    resource_pairs = (
        marketplace_models.Resource.objects.filter(offering__type=PLUGIN_NAME)
        .exclude(state__in=INVALID_RESOURCE_STATES)
        .values('offering', 'project')
        .distinct()
    )
    for pair in resource_pairs:
        try:
            project = structure_models.Project.available_objects.get(pk=pair['project'])
        except structure_models.Project.DoesNotExist:
            logger.debug(
                f'Skipping resource from a removed project with PK {pair["project"]}'
            )
            continue
        offering = marketplace_models.Offering.objects.get(pk=pair['offering'])
        projects_with_offerings[project].add(offering)

    order_item_pairs = (
        marketplace_models.OrderItem.objects.filter(
            offering__type=PLUGIN_NAME,
            state__in=(
                marketplace_models.OrderItem.States.PENDING,
                marketplace_models.OrderItem.States.EXECUTING,
            ),
        )
        .values('offering', 'order__project')
        .distinct()
    )
    for pair in order_item_pairs:
        try:
            project = structure_models.Project.available_objects.get(
                pk=pair['order__project']
            )
        except structure_models.Project.DoesNotExist:
            logger.debug(
                f'Skipping order item from a removed project with PK {pair["order__project"]}'
            )
            continue
        offering = marketplace_models.Offering.objects.get(pk=pair['offering'])
        projects_with_offerings[project].add(offering)

    return projects_with_offerings


def get_remote_project(offering, project, client=None):
    if not client:
        client = get_client_for_offering(offering)
    remote_project_uuid = get_project_backend_id(project)
    remote_projects = client.list_projects({'backend_id': remote_project_uuid})
    if len(remote_projects) == 0:
        return None
    elif len(remote_projects) == 1:
        return remote_projects[0]
    else:
        raise ValidationError('There are multiple projects in remote Waldur.')


def create_remote_project(offering, project, client=None):
    if not client:
        client = get_client_for_offering(offering)
    options = offering.secret_options
    remote_customer_uuid = options['customer_uuid']
    remote_project_name = f'{project.customer.name} / {project.name}'
    remote_project_uuid = get_project_backend_id(project)
    return client.create_project(
        customer_uuid=remote_customer_uuid,
        name=remote_project_name,
        backend_id=remote_project_uuid,
        description=project.description,
        end_date=project.end_date and project.end_date.isoformat(),
        oecd_fos_2007_code=project.oecd_fos_2007_code,
        is_industry=project.is_industry,
        type_uuid=project.type and project.type.uuid.hex,
    )


def get_or_create_remote_project(offering, project, client=None):
    remote_project = get_remote_project(offering, project, client)
    if not remote_project:
        remote_project = create_remote_project(offering, project, client)
        return remote_project, True
    else:
        return remote_project, False


def update_remote_project(request):
    client = get_client_for_offering(request.offering)
    remote_project_name = f'{request.project.customer.name} / {request.new_name}'
    remote_project_uuid = get_project_backend_id(request.project)
    remote_projects = client.list_projects({'backend_id': remote_project_uuid})
    if len(remote_projects) == 1:
        remote_project = remote_projects[0]
        payload = dict(
            name=remote_project_name,
            description=request.new_description,
            end_date=request.new_end_date and request.new_end_date.isoformat(),
            oecd_fos_2007_code=request.new_oecd_fos_2007_code,
            is_industry=request.new_is_industry,
        )
        if any(remote_project.get(key) != value for key, value in payload.items()):
            client.update_project(project_uuid=remote_project['uuid'], **payload)


def create_or_update_project_permission(
    client, remote_project_uuid, remote_user_uuid, role, expiration_time
):
    permissions = client.get_project_permissions(
        remote_project_uuid, remote_user_uuid, role
    )
    if not permissions:
        return client.create_project_permission(
            remote_user_uuid,
            remote_project_uuid,
            role,
            expiration_time.isoformat() if expiration_time else expiration_time,
        )
    permission = permissions[0]
    old_expiration_time = (
        dateparse.parse_datetime(permission['expiration_time'])
        if permission['expiration_time']
        else permission['expiration_time']
    )
    if old_expiration_time != expiration_time:
        return client.update_project_permission(
            permission['pk'],
            expiration_time.isoformat() if expiration_time else expiration_time,
        )


def remove_project_permission(client, remote_project_uuid, remote_user_uuid, role):
    remote_permissions = client.get_project_permissions(
        remote_project_uuid, remote_user_uuid, role
    )
    if remote_permissions:
        client.remove_project_permission(remote_permissions[0]['pk'])
        return True
    return False


def sync_project_permission(grant, project, role, user, expiration_time):
    for offering in get_remote_offerings_for_project(project):
        client = get_client_for_offering(offering)
        try:
            remote_user_uuid = client.get_remote_eduteams_user(user.username)['uuid']
        except WaldurClientException as e:
            logger.debug(
                f'Unable to fetch remote user {user.username} in offering {offering}: {e}'
            )
            continue

        try:
            remote_project, _ = get_or_create_remote_project(offering, project, client)
            remote_project_uuid = remote_project['uuid']
        except WaldurClientException as e:
            logger.debug(
                f'Unable to create remote project {project} in offering {offering}: {e}'
            )
            continue

        if grant:
            try:
                create_or_update_project_permission(
                    client,
                    remote_project_uuid,
                    remote_user_uuid,
                    role,
                    expiration_time,
                )
            except WaldurClientException as e:
                logger.debug(
                    f'Unable to create permission for user [{remote_user_uuid}] with role {role} (until {expiration_time}) '
                    f'and project [{remote_project_uuid}] in offering [{offering}]: {e}'
                )
        else:
            try:
                remove_project_permission(
                    client, remote_project_uuid, remote_user_uuid, role
                )
            except WaldurClientException as e:
                logger.debug(
                    f'Unable to remove permission for user [{remote_user_uuid}] with role {role} '
                    f'and project [{remote_project_uuid}] in offering [{offering}]: {e}'
                )


def push_project_users(offering, project, remote_project_uuid):
    client = get_client_for_offering(offering)

    permissions = collect_local_permissions(offering, project)

    for username, (role, expiration_time) in permissions.items():
        try:
            remote_user_uuid = client.get_remote_eduteams_user(username)['uuid']
        except WaldurClientException as e:
            logger.debug(
                f'Unable to fetch remote user {username} in offering {offering}: {e}'
            )
            continue

        try:
            create_or_update_project_permission(
                client, remote_project_uuid, remote_user_uuid, role, expiration_time
            )
        except WaldurClientException as e:
            logger.debug(
                f'Unable to create permission for user [{remote_user_uuid}] with role {role} '
                f'and project [{remote_project_uuid}] in offering [{offering}]: {e}'
            )


def collect_local_permissions(offering, project):
    permissions = defaultdict()
    for permission in structure_models.ProjectPermission.objects.filter(
        project=project, is_active=True, user__registration_method='eduteams'
    ):
        permissions[permission.user.username] = (
            permission.role,
            permission.expiration_time,
        )
    # Skip mapping for owners if offering belongs to the same customer
    if offering.customer == project.customer:
        return permissions
    for permission in structure_models.CustomerPermission.objects.filter(
        customer=project.customer,
        is_active=True,
        role=structure_models.CustomerRole.OWNER,
        user__registration_method='eduteams',
    ):
        # Organization owner is mapped to project manager in remote Waldur
        permissions[permission.user.username] = (
            structure_models.ProjectRole.MANAGER,
            permission.expiration_time,
        )
    return permissions


def parse_resource_state(serialized_state):
    return {v: k for (k, v) in marketplace_models.Resource.States.CHOICES}[
        serialized_state
    ]


def parse_order_state(serialized_state):
    return {v: k for (k, v) in marketplace_models.Order.States.CHOICES}[
        serialized_state
    ]


def parse_order_item_state(serialized_state):
    return {v: k for (k, v) in marketplace_models.OrderItem.States.CHOICES}[
        serialized_state
    ]


def parse_order_item_type(serialized_state):
    return {v: k for (k, v) in marketplace_models.OrderItem.Types.CHOICES}[
        serialized_state
    ]


def import_order(remote_order, project):
    approved_at = None
    if 'approved_at' in remote_order and remote_order['approved_at'] is not None:
        approved_at = remote_order['approved_at']
    return marketplace_models.Order.objects.create(
        project=project,
        state=parse_order_state(remote_order['state']),
        created_by=get_system_robot(),
        created=parse_datetime(remote_order['created']),
        approved_by=get_system_robot(),
        approved_at=approved_at,
    )


def import_order_item(remote_order_item, local_order, resource, remote_order_uuid):
    return marketplace_models.OrderItem.objects.create(
        order=local_order,
        resource=resource,
        type=parse_order_item_type(remote_order_item['type']),
        offering=resource.offering,
        # NB: As a backend_id of local OrderItem, uuid of a remote Order is used
        backend_id=remote_order_uuid,
        attributes=remote_order_item.get('attributes', {}),
        error_message=remote_order_item.get('error_message', ''),
        error_traceback=remote_order_item.get('error_traceback', ''),
        state=parse_order_item_state(remote_order_item['state']),
        created=parse_datetime(remote_order_item['created']),
        reviewed_by=get_system_robot(),
    )


def get_new_order_ids(client, backend_id):
    remote_order_items = client.list_order_items(
        {'resource_uuid': backend_id, 'field': ['order_uuid']}
    )
    local_order_ids = set(
        marketplace_models.OrderItem.objects.filter(
            resource__backend_id=backend_id
        ).values_list('backend_id', flat=True)
    )
    remote_order_ids = {order_item['order_uuid'] for order_item in remote_order_items}
    return remote_order_ids - local_order_ids


def import_resource_order_items(resource):
    if not resource.backend_id:
        return []
    client = get_client_for_offering(resource.offering)
    new_order_ids = get_new_order_ids(client, resource.backend_id)
    imported_order_items = []
    for order_id in new_order_ids:
        remote_order = client.get_order(order_id)
        local_order = import_order(remote_order, resource.project)
        for remote_order_item in remote_order['items']:
            local_order_item = import_order_item(
                remote_order_item, local_order, resource, order_id
            )
            imported_order_items.append(local_order_item)
    return imported_order_items


def pull_resource_state(local_resource):
    if not local_resource.backend_id:
        return
    client = get_client_for_offering(local_resource.offering)
    remote_resource = client.get_marketplace_resource(local_resource.backend_id)
    remote_state = parse_resource_state(remote_resource['state'])
    if local_resource.state != remote_state:
        local_resource.state = remote_state
        local_resource.save(update_fields=['state'])


def import_offering_components(local_offering, remote_offering):
    local_components_map = {}
    for remote_component in remote_offering['components']:
        local_component = marketplace_models.OfferingComponent.objects.create(
            offering=local_offering,
            **{key: remote_component[key] for key in OFFERING_COMPONENT_FIELDS},
        )
        local_components_map[local_component.type] = local_component
        logger.info(
            'Component %s (type: %s) for offering %s has been created',
            local_component,
            local_component.type,
            local_offering,
        )
    return local_components_map


def import_plans(local_offering, remote_offering, local_components_map):
    for remote_plan in remote_offering['plans']:
        local_plan = marketplace_models.Plan.objects.create(
            offering=local_offering,
            backend_id=remote_plan['uuid'],
            **{key: remote_plan[key] for key in PLAN_FIELDS},
        )
        remote_prices = remote_plan['prices']
        remote_quotas = remote_plan['quotas']
        components = set(remote_prices.keys()) | set(remote_quotas.keys())
        for component_type in components:
            plan_component = marketplace_models.PlanComponent.objects.create(
                plan=local_plan,
                component=local_components_map[component_type],
                price=remote_prices[component_type],
                amount=remote_quotas[component_type],
            )

            logger.info(
                'Plan component %s in offering %s has been created',
                plan_component,
                local_offering,
            )


def import_offering_thumbnail(local_offering, remote_offering):
    thumbnail_url = remote_offering['thumbnail']
    if thumbnail_url:
        thumbnail_resp = requests.get(thumbnail_url)
        content = io.BytesIO(thumbnail_resp.content)
        file_name = urllib3.util.parse_url(thumbnail_url).path.split('/')[-1]
        local_offering.thumbnail.save(file_name, content)
    else:
        local_offering.thumbnail.delete()
    local_offering.save(update_fields=['thumbnail'])
