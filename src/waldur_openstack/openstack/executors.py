import logging

from celery import chain

from waldur_core.core import executors as core_executors
from waldur_core.core import tasks as core_tasks
from waldur_core.core import utils as core_utils
from waldur_core.structure import executors as structure_executors
from waldur_core.structure import models as structure_models

from . import models, tasks

logger = logging.getLogger(__name__)


class SecurityGroupCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(cls, security_group, serialized_security_group, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_security_group,
            'create_security_group',
            state_transition='begin_creating',
        )


class ServerGroupCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(cls, server_group, serialized_server_group, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_server_group,
            'create_server_group',
            state_transition='begin_creating',
        )


class ServerGroupDeleteExecutor(core_executors.DeleteExecutor):
    @classmethod
    def get_task_signature(cls, server_group, serialized_server_group, **kwargs):
        if server_group.backend_id:
            return core_tasks.BackendMethodTask().si(
                serialized_server_group,
                'delete_server_group',
                state_transition='begin_deleting',
            )
        else:
            return core_tasks.StateTransitionTask().si(
                serialized_server_group, state_transition='begin_deleting'
            )


class SecurityGroupUpdateExecutor(core_executors.UpdateExecutor):
    @classmethod
    def get_task_signature(cls, security_group, serialized_security_group, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_security_group,
            'update_security_group',
            state_transition='begin_updating',
        )


class SecurityGroupPullExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, security_group, serialized_security_group, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_security_group,
            'pull_security_group',
            state_transition='begin_updating',
        )


class ServerGroupPullExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, server_group, serialized_server_group, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_server_group,
            'pull_server_group',
            state_transition='begin_updating',
        )


class SecurityGroupDeleteExecutor(core_executors.BaseExecutor):
    """
    Security group is being deleted in the last task instead of
    using separate DeleteTask from DeleteExecutorMixin so that
    deletion is performed transactionally.
    """

    @classmethod
    def pre_apply(cls, instance, **kwargs):
        instance.schedule_deleting()
        instance.save(update_fields=['state'])

    @classmethod
    def get_failure_signature(
        cls, instance, serialized_instance, force=False, **kwargs
    ):
        return core_tasks.ErrorStateTransitionTask().s(serialized_instance)

    @classmethod
    def get_task_signature(cls, security_group, serialized_security_group, **kwargs):
        state_transition_task = core_tasks.StateTransitionTask().si(
            serialized_security_group, state_transition='begin_deleting'
        )
        detach_task = core_tasks.BackendMethodTask().si(
            serialized_security_group, 'detach_security_group_from_all_instances'
        )
        detach_ports_task = core_tasks.BackendMethodTask().si(
            serialized_security_group, 'detach_security_group_from_all_ports'
        )
        delete_task = core_tasks.BackendMethodTask().si(
            serialized_security_group, 'delete_security_group'
        )
        _tasks = [state_transition_task]
        if security_group.backend_id:
            _tasks.append(detach_task)
            _tasks.append(detach_ports_task)
            _tasks.append(delete_task)
        return chain(*_tasks)


class PushSecurityGroupRulesExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, security_group, serialized_security_group, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_security_group,
            'push_security_group_rules',
            state_transition='begin_updating',
        )


class TenantCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(
        cls, tenant, serialized_tenant, pull_security_groups=True, **kwargs
    ):
        """Create tenant, add user to it, create internal network, pull quotas"""
        # we assume that tenant one network and subnet after creation
        network = tenant.networks.first()
        subnet = network.subnets.first()
        serialized_network = core_utils.serialize_instance(network)
        serialized_subnet = core_utils.serialize_instance(subnet)
        creation_tasks = [
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                'create_tenant_safe',
                state_transition='begin_creating',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'add_admin_user_to_tenant'
            ),
            core_tasks.BackendMethodTask().si(serialized_tenant, 'create_tenant_user'),
            core_tasks.BackendMethodTask().si(
                serialized_network, 'create_network', state_transition='begin_creating'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_subnet, 'create_subnet', state_transition='begin_creating'
            ),
        ]
        quotas = tenant.quotas.all()
        quotas = {
            q.name: int(q.limit) if q.limit.is_integer() else q.limit for q in quotas
        }
        creation_tasks.append(
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'push_tenant_quotas', quotas
            )
        )
        # handle security groups
        # XXX: Create default security groups
        for security_group in tenant.security_groups.all():
            creation_tasks.append(
                SecurityGroupCreateExecutor.as_signature(security_group)
            )

        if pull_security_groups:
            creation_tasks.append(
                core_tasks.BackendMethodTask().si(
                    serialized_tenant, 'pull_tenant_security_groups'
                )
            )

        # initialize external network if it defined in service settings
        service_settings = tenant.service_settings
        customer = tenant.project.customer
        external_network_id = service_settings.get_option('external_network_id')

        try:
            customer_openstack = models.CustomerOpenStack.objects.get(
                settings=service_settings, customer=customer
            )
            external_network_id = customer_openstack.external_network_id
        except models.CustomerOpenStack.DoesNotExist:
            pass

        if external_network_id and not kwargs.get('skip_connection_extnet'):
            creation_tasks.append(
                core_tasks.BackendMethodTask().si(
                    serialized_tenant,
                    'connect_tenant_to_external_network',
                    external_network_id=external_network_id,
                )
            )
            creation_tasks.append(
                core_tasks.BackendMethodTask().si(
                    serialized_tenant,
                    backend_method='pull_tenant_routers',
                )
            )

        creation_tasks.append(
            core_tasks.BackendMethodTask().si(serialized_tenant, 'pull_tenant_quotas')
        )
        return chain(*creation_tasks)

    @classmethod
    def get_success_signature(cls, tenant, serialized_tenant, **kwargs):
        return tasks.TenantCreateSuccessTask().si(serialized_tenant)

    @classmethod
    def get_failure_signature(cls, tenant, serialized_tenant, **kwargs):
        return tasks.TenantCreateErrorTask().s(serialized_tenant)


class TenantImportExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        tasks = [
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                'add_admin_user_to_tenant',
                state_transition='begin_updating',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'create_or_update_tenant_user'
            ),
            core_tasks.BackendMethodTask().si(serialized_tenant, 'pull_tenant_quotas'),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant_floating_ips'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant_security_groups'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'import_tenant_networks'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'import_tenant_subnets'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'detect_external_network'
            ),
        ]

        service_settings = structure_models.ServiceSettings.objects.get(scope=tenant)
        serialized_service_settings = core_utils.serialize_instance(service_settings)
        create_service_settings = (
            structure_executors.ServiceSettingsCreateExecutor.get_task_signature(
                service_settings, serialized_service_settings
            )
        )

        return chain(*tasks) | create_service_settings

    @classmethod
    def get_success_signature(cls, tenant, serialized_tenant, **kwargs):
        service_settings = structure_models.ServiceSettings.objects.get(scope=tenant)
        serialized_service_settings = core_utils.serialize_instance(service_settings)
        tasks = [
            core_tasks.StateTransitionTask().si(
                serialized_tenant, state_transition='set_ok'
            ),
            core_tasks.StateTransitionTask().si(
                serialized_service_settings, state_transition='set_ok'
            ),
        ]

        return chain(*tasks)


class TenantUpdateExecutor(core_executors.UpdateExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        updated_fields = kwargs['updated_fields']
        if 'name' in updated_fields or 'description' in updated_fields:
            return core_tasks.BackendMethodTask().si(
                serialized_tenant, 'update_tenant', state_transition='begin_updating'
            )
        else:
            return core_tasks.StateTransitionTask().si(
                serialized_tenant, state_transition='begin_updating'
            )


class TenantDeleteExecutor(core_executors.DeleteExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        state_transition = core_tasks.StateTransitionTask().si(
            serialized_tenant, state_transition='begin_deleting'
        )
        if not tenant.backend_id:
            return state_transition

        cleanup_networks = cls.get_networks_cleanup_tasks(serialized_tenant)
        cleanup_instances = cls.get_instances_cleanup_tasks(serialized_tenant)
        cleanup_identities = cls.get_identity_cleanup_tasks(serialized_tenant)

        return chain(
            [state_transition]
            + cleanup_networks
            + cleanup_instances
            + cleanup_identities
        )

    @classmethod
    def get_networks_cleanup_tasks(cls, serialized_tenant):
        return [
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_floating_ips',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_routes',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_ports',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_routers',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='pull_tenant_routers',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_networks',
            ),
        ]

    @classmethod
    def get_instances_cleanup_tasks(cls, serialized_tenant):
        return [
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_security_groups',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_snapshots',
            ),
            core_tasks.PollBackendCheckTask().si(
                serialized_tenant,
                backend_check_method='are_all_tenant_snapshots_deleted',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_instances',
            ),
            core_tasks.PollBackendCheckTask().si(
                serialized_tenant,
                backend_check_method='are_all_tenant_instances_deleted',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_volumes',
            ),
            core_tasks.PollBackendCheckTask().si(
                serialized_tenant, backend_check_method='are_all_tenant_volumes_deleted'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_server_groups',
            ),
        ]

    @classmethod
    def get_identity_cleanup_tasks(cls, serialized_tenant):
        return [
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant_user',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant,
                backend_method='delete_tenant',
            ),
        ]


class TenantAllocateFloatingIPExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'allocate_floating_ip_address',
            state_transition='begin_updating',
        )


class FloatingIPCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(cls, floating_ip, serialized_floating_ip, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_floating_ip,
            'create_floating_ip',
            state_transition='begin_creating',
        )


class FloatingIPUpdateExecutor(core_executors.UpdateExecutor):
    @classmethod
    def get_task_signature(cls, floating_ip, serialized_floating_ip, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_floating_ip,
            'update_floating_ip_description',
            state_transition='begin_updating',
            serialized_description=kwargs.get('description'),
        )


class FloatingIPDeleteExecutor(core_executors.DeleteExecutor):
    @classmethod
    def get_task_signature(cls, floating_ip, serialized_floating_ip, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_floating_ip,
            'delete_floating_ip',
            state_transition='begin_deleting',
        )


class FloatingIPPullExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, floating_ip, serialized_floating_ip, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_floating_ip,
            'pull_floating_ip',
            state_transition='begin_updating',
        )


class FloatingIPAttachExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, floating_ip, serialized_floating_ip, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_floating_ip,
            'attach_floating_ip_to_port',
            state_transition='begin_updating',
            serialized_port=kwargs.get('port'),
        )


class FloatingIPDetachExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, floating_ip, serialized_floating_ip, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_floating_ip,
            'detach_floating_ip_from_port',
            state_transition='begin_updating',
        )


class TenantPullFloatingIPsExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'pull_tenant_floating_ips',
            state_transition='begin_updating',
        )


class TenantPushQuotasExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, quotas=None, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'push_tenant_quotas',
            quotas,
            state_transition='begin_updating',
        )


class TenantPullQuotasExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant, 'pull_tenant_quotas', state_transition='begin_updating'
        )


class TenantPullExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        service_settings = structure_models.ServiceSettings.objects.get(scope=tenant)
        serialized_settings = core_utils.serialize_instance(service_settings)
        return chain(
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant', state_transition='begin_updating'
            ),
            core_tasks.BackendMethodTask().si(serialized_tenant, 'pull_tenant_quotas'),
            # Some resources are synchronized from openstack to openstack_tenant via handlers,
            # so for pulling them needed use serialized_tenant
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant_floating_ips'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant_security_groups'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant_server_groups'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, 'pull_tenant_networks'
            ),
            core_tasks.IndependentBackendMethodTask().si(
                serialized_settings, 'pull_images'
            ),
            core_tasks.IndependentBackendMethodTask().si(
                serialized_settings, 'pull_flavors'
            ),
            core_tasks.IndependentBackendMethodTask().si(
                serialized_settings, 'pull_volume_types'
            ),
            core_tasks.BackendMethodTask().si(serialized_tenant, 'pull_subnets'),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, backend_method='pull_tenant_routers'
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, backend_method='pull_tenant_ports'
            ),
        )

    @classmethod
    def get_success_signature(cls, instance, serialized_instance, **kwargs):
        return chain(
            core_tasks.StateTransitionTask().si(
                serialized_instance,
                state_transition='set_ok',
                action='',
                action_details={},
            ),
            tasks.SendSignalTenantPullSucceeded().si(serialized_instance),
        )


class TenantPullSecurityGroupsExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'pull_tenant_security_groups',
            state_transition='begin_updating',
        )


class TenantPullServerGroupsExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'pull_tenant_server_groups',
            state_transition='begin_updating',
        )


class TenantDetectExternalNetworkExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'detect_external_network',
            state_transition='begin_updating',
        )


class TenantChangeUserPasswordExecutor(core_executors.ActionExecutor):
    @classmethod
    def get_task_signature(cls, tenant, serialized_tenant, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_tenant,
            'change_tenant_user_password',
            state_transition='begin_updating',
        )


class RouterSetRoutesExecutor(core_executors.ActionExecutor):
    action = 'set_static_routes'

    @classmethod
    def get_task_signature(cls, router, serialized_router, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_router, 'set_static_routes', state_transition='begin_updating'
        )


class NetworkCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(cls, network, serialized_network, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_network, 'create_network', state_transition='begin_creating'
        )


class NetworkUpdateExecutor(core_executors.UpdateExecutor):
    @classmethod
    def get_task_signature(cls, network, serialized_network, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_network, 'update_network', state_transition='begin_updating'
        )


class NetworkDeleteExecutor(core_executors.DeleteExecutor):
    @classmethod
    def get_task_signature(cls, network, serialized_network, **kwargs):
        if network.backend_id:
            return core_tasks.BackendMethodTask().si(
                serialized_network, 'delete_network', state_transition='begin_deleting'
            )
        else:
            return core_tasks.StateTransitionTask().si(
                serialized_network, state_transition='begin_deleting'
            )


class NetworkPullExecutor(core_executors.ActionExecutor):
    action = 'pull'

    @classmethod
    def get_task_signature(cls, network, serialized_network, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_network, 'pull_network', state_transition='begin_updating'
        )


class SetMtuExecutor(core_executors.ActionExecutor):
    action = 'set_mtu'

    @classmethod
    def get_task_signature(cls, network, serialized_network, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_network, 'set_network_mtu', state_transition='begin_updating'
        )


class SubNetCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(cls, subnet, serialized_subnet, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_subnet,
            'create_subnet',
            state_transition='begin_creating',
        )


class SubNetUpdateExecutor(core_executors.UpdateExecutor):
    @classmethod
    def get_task_signature(cls, subnet, serialized_subnet, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_subnet,
            'update_subnet',
            state_transition='begin_updating',
        )


class SubnetConnectExecutor(core_executors.ActionExecutor):
    action = 'connect'

    @classmethod
    def get_task_signature(cls, subnet, serialized_subnet, **kwargs):
        serialized_tenant = core_utils.serialize_instance(subnet.network.tenant)
        return chain(
            core_tasks.BackendMethodTask().si(
                serialized_subnet,
                'connect_subnet',
                state_transition='begin_updating',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, backend_method='pull_tenant_routers'
            ),
        )


class SubnetDisconnectExecutor(core_executors.ActionExecutor):
    action = 'disconnect'

    @classmethod
    def get_task_signature(cls, subnet, serialized_subnet, **kwargs):
        serialized_tenant = core_utils.serialize_instance(subnet.network.tenant)
        return chain(
            core_tasks.BackendMethodTask().si(
                serialized_subnet,
                'disconnect_subnet',
                state_transition='begin_updating',
            ),
            core_tasks.BackendMethodTask().si(
                serialized_tenant, backend_method='pull_tenant_routers'
            ),
        )


class SubNetDeleteExecutor(core_executors.DeleteExecutor):
    @classmethod
    def get_task_signature(cls, subnet, serialized_subnet, **kwargs):
        if subnet.backend_id:
            return core_tasks.BackendMethodTask().si(
                serialized_subnet, 'delete_subnet', state_transition='begin_deleting'
            )
        else:
            return core_tasks.StateTransitionTask().si(
                serialized_subnet, state_transition='begin_deleting'
            )


class SubNetPullExecutor(core_executors.ActionExecutor):
    action = 'pull'

    @classmethod
    def get_task_signature(cls, subnet, serialized_subnet, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_subnet, 'pull_subnet', state_transition='begin_updating'
        )


class OpenStackCleanupExecutor(structure_executors.BaseCleanupExecutor):
    executors = (
        (models.SecurityGroup, SecurityGroupDeleteExecutor),
        (models.FloatingIP, FloatingIPDeleteExecutor),
        (models.SubNet, SubNetDeleteExecutor),
        (models.Network, NetworkDeleteExecutor),
        (models.Tenant, TenantDeleteExecutor),
        (models.ServerGroup, ServerGroupDeleteExecutor),
    )


class PortCreateExecutor(core_executors.CreateExecutor):
    @classmethod
    def get_task_signature(cls, port, serialized_port, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_port,
            'create_port',
            state_transition='begin_creating',
            serialized_network=kwargs.get('network'),
        )


class PortDeleteExecutor(core_executors.DeleteExecutor):
    @classmethod
    def get_task_signature(cls, port, serialized_port, **kwargs):
        return core_tasks.BackendMethodTask().si(
            serialized_port,
            'delete_port',
            state_transition='begin_deleting',
        )
