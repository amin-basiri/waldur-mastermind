from waldur_core.core.models import User
from waldur_core.logging.loggers import EventLogger, event_logger
from waldur_core.structure import models


class CustomerEventLogger(EventLogger):
    customer = models.Customer

    class Meta:
        event_types = (
            'customer_deletion_succeeded',
            'customer_update_succeeded',
            'customer_creation_succeeded',
        )
        event_groups = {
            'customers': event_types,
        }

    @staticmethod
    def get_scopes(event_context):
        return {event_context['customer']}


class ProjectEventLogger(EventLogger):
    project = models.Project

    class Meta:
        event_types = (
            'project_deletion_triggered',
            'project_deletion_succeeded',
            'project_update_succeeded',
            'project_creation_succeeded',
        )
        event_groups = {
            'projects': event_types,
        }

    @staticmethod
    def get_scopes(event_context):
        project = event_context['project']
        return {project, project.customer}


class CustomerRoleEventLogger(EventLogger):
    customer = models.Customer
    affected_user = User
    user = User
    structure_type = str
    role_name = str

    class Meta:
        event_types = 'role_granted', 'role_revoked', 'role_updated'
        event_groups = {
            'customers': event_types,
            'users': event_types,
        }
        nullable_fields = ['user']

    @staticmethod
    def get_scopes(event_context):
        return {event_context['customer']}


class ProjectRoleEventLogger(EventLogger):
    project = models.Project
    user = User
    affected_user = User
    structure_type = str
    role_name = str

    class Meta:
        event_types = 'role_granted', 'role_revoked', 'role_updated'
        event_groups = {
            'projects': event_types,
            'users': event_types,
        }
        nullable_fields = ['user']

    @staticmethod
    def get_scopes(event_context):
        project = event_context['project']
        return {project, project.customer}


class ResourceEventLogger(EventLogger):
    resource = models.BaseResource

    class Meta:
        event_types = (
            'resource_start_scheduled',
            'resource_start_succeeded',
            'resource_start_failed',
            'resource_stop_scheduled',
            'resource_stop_succeeded',
            'resource_stop_failed',
            'resource_restart_scheduled',
            'resource_restart_succeeded',
            'resource_restart_failed',
            'resource_creation_scheduled',
            'resource_creation_succeeded',
            'resource_creation_failed',
            'resource_import_succeeded',
            'resource_update_succeeded',
            'resource_deletion_scheduled',
            'resource_deletion_succeeded',
            'resource_deletion_failed',
        )
        event_groups = {
            'resources': event_types,
        }

    @staticmethod
    def get_scopes(event_context):
        resource = event_context['resource']
        return {resource, resource.project, resource.project.customer}


event_logger.register('customer_role', CustomerRoleEventLogger)
event_logger.register('project_role', ProjectRoleEventLogger)
event_logger.register('customer', CustomerEventLogger)
event_logger.register('project', ProjectEventLogger)
event_logger.register('resource', ResourceEventLogger)
