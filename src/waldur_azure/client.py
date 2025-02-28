from dataclasses import dataclass
from typing import Dict, List

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    DiskCreateOption,
    LinuxConfiguration,
    OSProfile,
    SshConfiguration,
    SshPublicKey,
    VirtualMachine,
    VirtualMachineImage,
)
from azure.mgmt.consumption import ConsumptionManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.network.models import (
    NetworkInterface,
    NetworkInterfaceIPConfiguration,
    NetworkSecurityGroup,
    SecurityRule,
)
from azure.mgmt.rdbms.postgresql import PostgreSQLManagementClient
from azure.mgmt.rdbms.postgresql.models import (
    ServerForCreate,
    ServerPropertiesForDefaultCreate,
    ServerVersion,
    StorageProfile,
)
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.storage.models import Kind, Sku, SkuName, StorageAccountCreateParameters
from django.utils.functional import cached_property
from msrest.exceptions import ClientException

from waldur_core.structure.exceptions import ServiceBackendError


class AzureBackendError(ServiceBackendError):
    pass


@dataclass
class AzureImage:
    image: VirtualMachineImage
    publisher_name: str
    offer_name: str
    sku_name: str
    version_name: str


class AzureClient:
    def __init__(self, settings):
        self.subscription_id = str(settings.options['subscription_id'])
        self.client_id = str(settings.options['client_id'])
        self.client_secret = str(settings.options['client_secret'])
        self.tenant_id = str(settings.options['tenant_id'])

    @cached_property
    def credentials(self):
        return ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

    @cached_property
    def subscription_client(self):
        return SubscriptionClient(self.credentials)

    @cached_property
    def resource_client(self):
        return ResourceManagementClient(self.credentials, self.subscription_id)

    @cached_property
    def compute_client(self):
        return ComputeManagementClient(
            self.credentials,
            self.subscription_id,
        )

    @cached_property
    def storage_client(self):
        return StorageManagementClient(self.credentials, self.subscription_id)

    @cached_property
    def network_client(self):
        return NetworkManagementClient(self.credentials, self.subscription_id)

    @cached_property
    def consumption_client(self):
        return ConsumptionManagementClient(self.credentials, self.subscription_id)

    @cached_property
    def pgsql_client(self):
        return PostgreSQLManagementClient(self.credentials, self.subscription_id)

    def list_locations(self):
        try:
            return self.subscription_client.subscriptions.list_locations(
                self.subscription_id
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_resource_group_locations(self):
        """
        Resource Manager is supported in all regions, but the resources
        you deploy might not be supported in all regions.
        In addition, there may be limitations on your subscription that
        prevent you from using some regions that support the resource.

        See also: https://docs.microsoft.com/en-us/azure/azure-resource-manager/resource-manager-supported-services
        """
        try:
            provider = self.resource_client.providers.get('Microsoft.Resources')
        except ClientException as exc:
            raise AzureBackendError(exc)
        else:
            for resource in provider.resource_types:
                if resource.resource_type == 'resourceGroups':
                    return resource.locations

    def list_resource_groups(self):
        try:
            return self.resource_client.resource_groups.list()
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_resource_group(self, location, resource_group_name):
        try:
            return self.resource_client.resource_groups.create_or_update(
                resource_group_name, {'location': location}
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def delete_resource_group(self, resource_group_name):
        try:
            return self.resource_client.resource_groups.begin_delete(
                resource_group_name
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_virtual_machine_sizes(self, location):
        try:
            return self.compute_client.virtual_machine_sizes.list(location)
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_virtual_machine_size_availability_zones(
        self, location: str
    ) -> Dict[str, List[str]]:
        try:
            all_skus = self.compute_client.resource_skus.list(
                filter=f"location eq '{location}'"
            )
        except ClientException as exc:
            raise AzureBackendError(exc)
        vm_skus = [sku for sku in all_skus if sku.resource_type == 'virtualMachines']
        zones = dict()
        for sku in vm_skus:
            for location_info in sku.location_info:
                if location_info.location == location:
                    zones[sku.name] = location_info.zones
        return zones

    def list_virtual_machine_images(
        self, location, selected_provider=None
    ) -> List[AzureImage]:
        try:
            publishers = self.compute_client.virtual_machine_images.list_publishers(
                location
            )

            # TODO: Figure out a better way
            # XXX Fix a list of publishers we trust
            if selected_provider:
                publishers = list(
                    filter(lambda x: (x.name in selected_provider), publishers)
                )

            for publisher in publishers:
                offers = self.compute_client.virtual_machine_images.list_offers(
                    location,
                    publisher.name,
                )

                for offer in offers:
                    skus = self.compute_client.virtual_machine_images.list_skus(
                        location,
                        publisher.name,
                        offer.name,
                    )

                    for sku in skus:
                        result_list = self.compute_client.virtual_machine_images.list(
                            location,
                            publisher.name,
                            offer.name,
                            sku.name,
                        )

                        for version in result_list:
                            yield AzureImage(
                                self.compute_client.virtual_machine_images.get(
                                    location,
                                    publisher.name,
                                    offer.name,
                                    sku.name,
                                    version.name,
                                ),
                                publisher.name,
                                offer.name,
                                sku.name,
                                version.name,
                            )

        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_all_virtual_machines(self):
        try:
            return self.compute_client.virtual_machines.list_all()
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_virtual_machines_in_group(self, resource_group_name):
        try:
            return self.compute_client.virtual_machines.list(resource_group_name)
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_virtual_machine(self, resource_group_name, vm_name) -> VirtualMachine:
        try:
            return self.compute_client.virtual_machines.get(
                resource_group_name, vm_name
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_virtual_machine(
        self,
        location,
        resource_group_name,
        vm_name,
        size_name,
        nic_id,
        image_reference,
        username,
        password,
        custom_data=None,
        ssh_key=None,
    ):
        os_profile = OSProfile(
            computer_name=vm_name,
            admin_username=username,
            admin_password=password,
        )
        if custom_data:
            os_profile.custom_data = custom_data

        if ssh_key:
            os_profile.linux_configuration = LinuxConfiguration(
                ssh=SshConfiguration(
                    public_keys=[
                        SshPublicKey(key_data=ssh_key),
                    ],
                )
            )
        try:
            return self.compute_client.virtual_machines.begin_create_or_update(
                resource_group_name,
                vm_name,
                {
                    'location': location,
                    'os_profile': os_profile,
                    'hardware_profile': {'vm_size': size_name},
                    'storage_profile': {
                        'image_reference': {
                            'publisher': image_reference['publisher'],
                            'offer': image_reference['offer'],
                            'sku': image_reference['sku'],
                            'version': image_reference['version'],
                        },
                    },
                    'network_profile': {
                        'network_interfaces': [
                            {
                                'id': nic_id,
                            }
                        ]
                    },
                },
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def delete_virtual_machine(self, resource_group_name, vm_name):
        try:
            return self.compute_client.virtual_machines.begin_delete(
                resource_group_name,
                vm_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def start_virtual_machine(self, resource_group_name, vm_name):
        try:
            return self.compute_client.virtual_machines.begin_start(
                resource_group_name,
                vm_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def restart_virtual_machine(self, resource_group_name, vm_name):
        try:
            return self.compute_client.virtual_machines.begin_restart(
                resource_group_name,
                vm_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def stop_virtual_machine(self, resource_group_name, vm_name):
        try:
            return self.compute_client.virtual_machines.begin_power_off(
                resource_group_name,
                vm_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_storage_account(self, location, resource_group_name, account_name):
        try:
            return self.storage_client.storage_accounts.begin_create(
                resource_group_name,
                account_name,
                StorageAccountCreateParameters(
                    sku=Sku(name=SkuName.standard_ragrs),
                    kind=Kind.storage,
                    location=location,
                    enable_https_traffic_only=True,
                ),
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_disk(self, location, resource_group_name, disk_name, disk_size_gb):
        try:
            return self.compute_client.disks.begin_create_or_update(
                resource_group_name,
                disk_name,
                {
                    'location': location,
                    'disk_size_gb': disk_size_gb,
                    'creation_data': {'create_option': DiskCreateOption.empty},
                },
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_network(self, location, resource_group_name, network_name, cidr):
        try:
            return self.network_client.virtual_networks.begin_create_or_update(
                resource_group_name,
                network_name,
                {'location': location, 'address_space': {'address_prefixes': [cidr]}},
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_subnet(self, resource_group_name, network_name, subnet_name, cidr):
        try:
            return self.network_client.subnets.begin_create_or_update(
                resource_group_name,
                network_name,
                subnet_name,
                {
                    'address_prefix': cidr,
                },
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_subnet(self, resource_group_name, network_name, subnet_name):
        try:
            return self.network_client.subnets.get(
                resource_group_name,
                network_name,
                subnet_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_network(self, resource_group_name, network_name):
        try:
            return self.network_client.virtual_networks.get(
                resource_group_name,
                network_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_network_interface(self, resource_group_name, network_interface_name):
        try:
            return self.network_client.network_interfaces.get(
                resource_group_name,
                network_interface_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_network_interface(
        self,
        location,
        resource_group_name,
        interface_name,
        config_name,
        subnet_id,
        public_ip_id=None,
        security_group_id=None,
    ):
        ip_configuration = NetworkInterfaceIPConfiguration(
            name=config_name, subnet={'id': subnet_id}
        )

        if public_ip_id:
            ip_configuration.public_ip_address = {'id': public_ip_id}

        interface_parameters = NetworkInterface(
            location=location,
            ip_configurations=[ip_configuration],
        )

        if security_group_id:
            interface_parameters.network_security_group = {'id': security_group_id}

        try:
            return self.network_client.network_interfaces.begin_create_or_update(
                resource_group_name, interface_name, interface_parameters
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_ssh_security_group(
        self, location, resource_group_name, network_security_group_name
    ):
        ssh_rule = SecurityRule(
            name='default-allow-ssh',
            protocol='Tcp',
            source_port_range='*',
            destination_port_range=22,
            direction='Inbound',
            source_address_prefix='*',
            destination_address_prefix='*',
            access='Allow',
            priority=1000,
        )

        security_group = NetworkSecurityGroup(
            location=location, security_rules=[ssh_rule]
        )

        try:
            return self.network_client.network_security_groups.begin_create_or_update(
                resource_group_name,
                network_security_group_name,
                security_group,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_public_ip(self, resource_group_name: str, public_ip_address_name: str):
        try:
            return self.network_client.public_ip_addresses.get(
                resource_group_name, public_ip_address_name
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_all_public_ips(self):
        try:
            return self.network_client.public_ip_addresses.list_all()
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_public_ip(self, location, resource_group_name, public_ip_address_name):
        try:
            return self.network_client.public_ip_addresses.begin_create_or_update(
                resource_group_name, public_ip_address_name, {'location': location}
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def delete_public_ip(self, resource_group_name, public_ip_address_name):
        try:
            return self.network_client.public_ip_addresses.begin_delete(
                resource_group_name, public_ip_address_name
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_all_sql_servers(self):
        try:
            return self.pgsql_client.servers.list()
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_sql_servers_in_group(self, resource_group_name):
        try:
            return self.pgsql_client.servers.list_by_resource_group(resource_group_name)
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_sql_server(self, resource_group_name, server_name):
        try:
            return self.pgsql_client.servers.get_by_resource_group(
                resource_group_name,
                server_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_sql_server(
        self,
        location,
        resource_group_name,
        server_name,
        username,
        password,
        sku=None,
        storage_mb=None,
        ssl_enforcement=None,
    ):
        properties = ServerPropertiesForDefaultCreate(
            administrator_login=username,
            administrator_login_password=password,
            version=ServerVersion.nine_full_stop_six,
            ssl_enforcement=ssl_enforcement,
        )
        if storage_mb:
            properties.storage_profile = StorageProfile(storage_mb=storage_mb)
        try:
            poller = self.pgsql_client.servers.begin_create(
                resource_group_name,
                server_name,
                ServerForCreate(
                    properties=properties,
                    location=location,
                    sku=sku,
                ),
            )
            return poller.result()
        except ClientException as exc:
            raise AzureBackendError(exc)

    def delete_sql_server(self, resource_group_name, server_name):
        try:
            return self.pgsql_client.servers.begin_delete(
                resource_group_name, server_name
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_sql_firewall_rule(
        self,
        resource_group_name,
        server_name,
        firewall_rule_name,
        start_ip_address,
        end_ip_address,
    ):
        try:
            poller = self.pgsql_client.firewall_rules.begin_create_or_update(
                resource_group_name,
                server_name,
                firewall_rule_name,
                start_ip_address,
                end_ip_address,
            )
            return poller.result()
        except ClientException as exc:
            raise AzureBackendError(exc)

    def get_sql_database(self, resource_group_name, server_name, database_name):
        try:
            return self.pgsql_client.databases.get(
                resource_group_name,
                server_name,
                database_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def create_sql_database(
        self,
        resource_group_name,
        server_name,
        database_name,
        charset=None,
        collation=None,
    ):
        try:
            return self.pgsql_client.databases.begin_create_or_update(
                resource_group_name,
                server_name,
                database_name,
                charset,
                collation,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def list_sql_databases_in_server(self, resource_group_name, server_name):
        try:
            return self.pgsql_client.databases.list_by_server(
                resource_group_name,
                server_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)

    def delete_sql_database(self, resource_group_name, server_name, database_name):
        try:
            return self.pgsql_client.databases.begin_delete(
                resource_group_name,
                server_name,
                database_name,
            )
        except ClientException as exc:
            raise AzureBackendError(exc)
