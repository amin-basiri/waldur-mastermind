import datetime
from decimal import Decimal
from unittest import mock

from ddt import data, ddt
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.template import Context, Template
from django.test import override_settings
from freezegun import freeze_time
from rest_framework import status, test

from waldur_core.core import utils as core_utils
from waldur_core.core.utils import month_end
from waldur_core.structure.tests import fixtures
from waldur_mastermind.invoices import models as invoices_models
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace import tasks as marketplace_tasks
from waldur_mastermind.marketplace.tests import factories as marketplace_factories
from waldur_mastermind.marketplace.tests import utils as test_utils
from waldur_mastermind.marketplace_support import PLUGIN_NAME
from waldur_mastermind.marketplace_support.utils import get_order_item_issue
from waldur_mastermind.support import models as support_models
from waldur_mastermind.support.log import IssueEventLogger
from waldur_mastermind.support.tests import factories as support_factories
from waldur_mastermind.support.tests.base import BaseTest


class RequestCreateTest(BaseTest):
    def test_request_is_created_when_order_item_is_processed(self):
        fixture = fixtures.ProjectFixture()
        offering = marketplace_factories.OfferingFactory(
            type=PLUGIN_NAME, options={'order': []}
        )

        order_item = marketplace_factories.OrderItemFactory(
            offering=offering,
            attributes={'name': 'item_name', 'description': 'Description'},
        )

        serialized_order = core_utils.serialize_instance(order_item.order)
        serialized_user = core_utils.serialize_instance(fixture.staff)
        marketplace_tasks.process_order(serialized_order, serialized_user)

        self.assertTrue(
            marketplace_models.Resource.objects.filter(name='item_name').exists()
        )
        resource = marketplace_models.Resource.objects.get(name='item_name')
        order_item = marketplace_models.OrderItem.objects.get(resource=resource)
        self.assertTrue(
            support_models.Issue.objects.filter(
                resource_object_id=order_item.id
            ).exists()
        )

    def test_request_payload_is_validated(self):
        self.fixture = fixtures.ProjectFixture()
        self.project = self.fixture.project
        self.user = self.fixture.staff
        self.offering = marketplace_factories.OfferingFactory()

        order_item = marketplace_factories.OrderItemFactory(
            offering=self.offering,
            attributes={'name': 'item_name', 'description': '{}'},
        )
        url = marketplace_factories.OrderFactory.get_url(order_item.order, 'approve')

        self.client.force_login(self.user)
        response = self.client.post(url)
        self.assertTrue(response.status_code, status.HTTP_400_BAD_REQUEST)

    def submit_order_item(self):
        fixture = fixtures.ProjectFixture()
        offering = marketplace_factories.OfferingFactory(
            type=PLUGIN_NAME, options={'order': []}
        )

        order_item = marketplace_factories.OrderItemFactory(
            offering=offering,
            attributes={'name': 'item_name', 'description': 'Description'},
        )
        offering_component = marketplace_factories.OfferingComponentFactory(
            name='CORES'
        )
        marketplace_factories.PlanComponentFactory(
            component=offering_component, plan=order_item.plan
        )

        serialized_order = core_utils.serialize_instance(order_item.order)
        serialized_user = core_utils.serialize_instance(fixture.staff)
        marketplace_tasks.process_order(serialized_order, serialized_user)
        return order_item

    def test_add_backlink_to_order_item_details_into_created_service_desk_ticket(self):
        order_item = self.submit_order_item()
        self.assertTrue(
            marketplace_models.Resource.objects.filter(name='item_name').exists()
        )
        issue = get_order_item_issue(order_item)
        order_item_url = core_utils.format_homeport_link(
            'projects/{project_uuid}/marketplace-order-item-details/{order_item_uuid}/',
            order_item_uuid=order_item.uuid.hex,
            project_uuid=order_item.order.project.uuid,
        )
        self.assertTrue(order_item_url in issue.description)

    def test_resource_name_is_propagated(self):
        self.submit_order_item()
        resource = marketplace_models.Resource.objects.get(name='item_name')
        self.assertEqual(resource.attributes['name'], 'item_name')

    def test_description_formatting(self):
        order_item = self.submit_order_item()
        resource = marketplace_models.Resource.objects.get(name='item_name')
        issue = get_order_item_issue(order_item)
        self.assertTrue('Order item' in issue.description)
        self.assertTrue(resource.plan.name in issue.description)
        self.assertTrue(
            resource.plan.components.first().component.name in issue.description
        )
        self.assertTrue(order_item.order.created_by.full_name in issue.description)
        self.assertTrue(order_item.order.created_by.civil_number in issue.description)
        self.assertTrue(order_item.order.created_by.email in issue.description)

    def test_service_provider_name_is_propagated(self):
        order_item = self.submit_order_item()
        name = order_item.offering.customer.name
        resource = marketplace_models.Resource.objects.get(name='item_name')
        order_item = marketplace_models.OrderItem.objects.get(resource=resource)
        issue = get_order_item_issue(order_item)
        self.assertTrue(name in issue.description)

    def test_resource_UUID_is_propagated(self):
        order_item = self.submit_order_item()
        order_item.refresh_from_db()
        resource = order_item.resource
        issue = get_order_item_issue(order_item)
        self.assertTrue(resource.uuid.hex in issue.description)

    def test_issue_caller_is_equal_order_created_by(self):
        fixture = fixtures.ProjectFixture()
        offering = marketplace_factories.OfferingFactory(
            type=PLUGIN_NAME, options={'order': []}
        )

        order_item = marketplace_factories.OrderItemFactory(
            offering=offering,
            attributes={'name': 'item_name', 'description': 'Description'},
        )

        serialized_order = core_utils.serialize_instance(order_item.order)
        serialized_user = core_utils.serialize_instance(fixture.staff)
        marketplace_tasks.process_order(serialized_order, serialized_user)

        resource = marketplace_models.Resource.objects.get(name='item_name')
        order_item = marketplace_models.OrderItem.objects.get(resource=resource)
        issue = get_order_item_issue(order_item)
        self.assertEqual(issue.caller, order_item.order.created_by)

    def test_order_item_serializer_includes_issue_link(self):
        order_item = self.submit_order_item()
        issue = get_order_item_issue(order_item)
        issue.key = 'SUP-123'
        issue.save()
        self.client.force_authenticate(self.fixture.staff)
        url = marketplace_factories.OrderItemFactory.get_url(order_item=order_item)
        resource = self.client.get(url)
        self.assertEqual(
            resource.data['issue'],
            {'key': 'SUP-123', 'uuid': issue.uuid.hex},
        )

    def test_create_confirmation_comment_if_offering_template_is_defined(self):
        fixture = fixtures.ProjectFixture()
        offering = marketplace_factories.OfferingFactory(
            type=PLUGIN_NAME,
            options={'order': []},
            secret_options={
                'template_confirmation_comment': 'template_confirmation_comment'
            },
        )

        order_item = marketplace_factories.OrderItemFactory(
            offering=offering,
            attributes={'name': 'item_name', 'description': 'Description'},
        )

        serialized_order = core_utils.serialize_instance(order_item.order)
        serialized_user = core_utils.serialize_instance(fixture.staff)
        marketplace_tasks.process_order(serialized_order, serialized_user)

        self.mock_get_active_backend().create_confirmation_comment.assert_called_once_with(
            mock.ANY, 'template_confirmation_comment'
        )

    def test_set_creation_ticket_id_as_backend_id_of_resource(self):
        def mock_create_issue(issue):
            issue.backend_id = 'WAL TEST'
            issue.save()

        self.mock_get_active_backend().create_issue = mock_create_issue
        fixture = fixtures.ProjectFixture()
        offering = marketplace_factories.OfferingFactory(
            type=PLUGIN_NAME, options={'order': []}
        )

        order_item = marketplace_factories.OrderItemFactory(
            offering=offering,
            attributes={'name': 'item_name', 'description': 'Description'},
        )

        serialized_order = core_utils.serialize_instance(order_item.order)
        serialized_user = core_utils.serialize_instance(fixture.staff)
        marketplace_tasks.process_order(serialized_order, serialized_user)

        resource = marketplace_models.Resource.objects.get(name='item_name')
        issue = support_models.Issue.objects.get(resource_object_id=order_item.id)
        self.assertEqual(issue.backend_id, resource.backend_id)


@freeze_time('2019-01-01')
class RequestActionBaseTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.fixture = fixtures.ProjectFixture()
        self.project = self.fixture.project

        self.user = self.fixture.staff
        self.offering = marketplace_factories.OfferingFactory(
            state=marketplace_models.Offering.States.ACTIVE, type=PLUGIN_NAME
        )

        self.current_plan = marketplace_factories.PlanFactory(
            offering=self.offering, unit_price=10
        )
        self.offering_component = marketplace_factories.OfferingComponentFactory(
            offering=self.offering,
            billing_type=marketplace_models.OfferingComponent.BillingTypes.FIXED,
        )

        self.plan_component = marketplace_factories.PlanComponentFactory(
            plan=self.current_plan, component=self.offering_component
        )
        self.resource = marketplace_factories.ResourceFactory(
            name='test_request',
            project=self.project,
            offering=self.offering,
            plan=self.current_plan,
        )
        self.resource.set_state_ok()
        self.resource.save()

        self.success_issue_status = 'ok'
        support_factories.IssueStatusFactory(
            name=self.success_issue_status,
            type=support_models.IssueStatus.Types.RESOLVED,
        )

        self.error_issue_status = 'error'
        support_factories.IssueStatusFactory(
            name=self.error_issue_status, type=support_models.IssueStatus.Types.CANCELED
        )

        self.start = datetime.datetime.now()


@ddt
class RequestDeleteTest(RequestActionBaseTest):
    def test_success_terminate_resource_if_issue_is_resolved(self):
        order_item = self.get_order_item(self.success_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.DONE)
        self.assertEqual(self.mock_get_active_backend().create_issue.call_count, 1)
        self.assertEqual(
            self.mock_get_active_backend().create_issue_links.call_count, 1
        )
        self.resource.refresh_from_db()
        self.assertEqual(
            self.resource.state, marketplace_models.Resource.States.TERMINATED
        )
        self.assertEqual(order_item.order.state, marketplace_models.Order.States.DONE)

    def test_fail_termination_order_item_if_issue_is_canceled(self):
        order_item = self.get_order_item(self.error_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.ERRED)

    @data('staff', 'owner', 'admin', 'manager')
    def test_terminate_operation_is_available(self, user):
        user = getattr(self.fixture, user)
        response = self.request_resource_termination(user)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @data('user')
    def test_terminate_operation_is_not_available(self, user):
        user = getattr(self.fixture, user)
        response = self.request_resource_termination(user)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def request_resource_termination(self, user=None):
        user = user or self.user
        url = marketplace_factories.ResourceFactory.get_url(
            resource=self.resource, action='terminate'
        )
        self.client.force_authenticate(user)
        return self.client.post(url)

    def test_description_formatting(self):
        issue = self.get_issue()
        resource = issue.resource.resource
        self.assertTrue('Terminate resource' in issue.description)
        self.assertTrue(resource.plan.name in issue.description, issue.description)
        self.assertTrue(resource.uuid.hex in issue.description, issue.description)

    def get_issue(self):
        response = self.request_resource_termination()
        order = marketplace_models.Order.objects.get(uuid=response.data['order_uuid'])
        order_item = order.items.first()
        test_utils.process_order_item(order_item, self.user)

        order_item_content_type = ContentType.objects.get_for_model(order_item)
        return support_models.Issue.objects.get(
            resource_object_id=order_item.id,
            resource_content_type=order_item_content_type,
        )

    def get_order_item(self, issue_status):
        self.request_resource_termination()
        order = marketplace_models.Order.objects.get(project=self.project)
        order_item = order.items.first()
        if order_item.order.state != marketplace_models.Order.States.EXECUTING:
            order_item.order.approve()
            order_item.order.save()
        test_utils.process_order_item(order_item, self.user)

        issue = get_order_item_issue(order_item)
        issue.status = issue_status
        issue.save()
        return marketplace_models.OrderItem.objects.first()


@ddt
class RequestSwitchPlanTest(RequestActionBaseTest):
    def setUp(self):
        super().setUp()
        self.plan = marketplace_factories.PlanFactory(
            offering=self.offering, unit_price=50
        )
        marketplace_factories.PlanComponentFactory(
            plan=self.plan, component=self.offering_component, price=Decimal(50)
        )
        self.order_item = marketplace_factories.OrderItemFactory(resource=self.resource)
        self.order_item.set_state_executing()
        self.order_item.set_state_done()
        self.order_item.save()

    def test_success_switch_plan_if_issue_is_resolved(self):
        order_item = self.get_order_item(self.success_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.DONE)
        self.assertEqual(self.mock_get_active_backend().create_issue.call_count, 1)
        self.assertEqual(
            self.mock_get_active_backend().create_issue_links.call_count, 1
        )
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.state, marketplace_models.Resource.States.OK)
        self.assertEqual(self.resource.plan, self.plan)

    def test_add_links_to_previous_issues(self):
        create_issue = support_factories.IssueFactory(resource=self.order_item)
        order_item = self.get_order_item(self.success_issue_status)
        self.assertEqual(self.mock_get_active_backend().create_issue.call_count, 1)
        self.assertEqual(
            self.mock_get_active_backend().create_issue_links.call_count, 1
        )
        update_issue = get_order_item_issue(order_item)
        self.mock_get_active_backend().create_issue_links.assert_called_once_with(
            update_issue, list(support_models.Issue.objects.filter(id=create_issue.id))
        )

    def test_order_item_is_updated_when_issue_is_resolved(self):
        order_item = self.get_order_item(self.success_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.DONE)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.state, marketplace_models.Resource.States.OK)
        self.assertEqual(self.resource.plan, self.plan)

    def test_fail_switch_plan_if_issue_is_fail(self):
        order_item = self.get_order_item(self.error_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.ERRED)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.state, marketplace_models.Resource.States.ERRED)
        self.assertEqual(self.resource.plan, self.current_plan)

    @freeze_time('2019-01-15')
    def test_switch_invoice_item_if_plan_switched(self):
        self.get_order_item(self.success_issue_status)
        new_start = datetime.datetime.now()
        end = month_end(new_start)
        self.assertTrue(
            invoices_models.InvoiceItem.objects.filter(
                resource=self.resource,
                project=self.project,
                unit_price=Decimal(10),
                start=self.start,
                end=new_start,
            ).exists()
        )
        self.assertTrue(
            invoices_models.InvoiceItem.objects.filter(
                resource=self.resource,
                project=self.project,
                unit_price=Decimal(50),
                start=new_start,
                end=end,
            ).exists()
        )

    @data('staff', 'owner', 'admin', 'manager')
    def test_switch_plan_operation_is_available(self, user):
        user = getattr(self.fixture, user)
        response = self.request_switch_plan(user)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @data('user')
    def test_resource_is_not_available(self, user):
        user = getattr(self.fixture, user)
        response = self.request_switch_plan(user)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_resource_state_validation(self):
        self.resource.set_state_updating()
        self.resource.save()
        response = self.request_switch_plan()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_plan_validation(self):
        response = self.request_switch_plan(
            add_payload={'plan': marketplace_factories.PlanFactory.get_public_url()}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_description_formatting(self):
        issue = self.get_issue()
        resource = issue.resource.resource
        self.assertTrue('Switch plan for resource' in issue.description)
        self.assertTrue(resource.uuid.hex in issue.description)

    def request_switch_plan(self, user=None, add_payload=None):
        user = user or self.user
        url = marketplace_factories.ResourceFactory.get_url(
            resource=self.resource, action='switch_plan'
        )
        payload = {'plan': marketplace_factories.PlanFactory.get_public_url(self.plan)}

        if add_payload:
            payload.update(add_payload)

        self.client.force_authenticate(user)
        return self.client.post(url, payload)

    def get_issue(self):
        response = self.request_switch_plan()
        order = marketplace_models.Order.objects.get(uuid=response.data['order_uuid'])
        order_item = order.items.first()
        test_utils.process_order_item(order_item, self.user)
        return get_order_item_issue(order_item)

    def get_order_item(self, issue_status):
        response = self.request_switch_plan()
        order = marketplace_models.Order.objects.get(uuid=response.data['order_uuid'])
        order_item = order.items.first()
        test_utils.process_order_item(order_item, self.user)

        issue = get_order_item_issue(order_item)
        issue.status = issue_status
        issue.save()
        order_item.refresh_from_db()
        return order_item


@ddt
class UpdateLimitsTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.old_limits = {'cpu': 10}
        self.new_limits = {'cpu': 20}

        self.fixture = fixtures.ProjectFixture()
        self.project = self.fixture.project

        self.user = self.fixture.staff
        self.offering = marketplace_factories.OfferingFactory(
            state=marketplace_models.Offering.States.ACTIVE, type=PLUGIN_NAME
        )

        self.offering_component = marketplace_factories.OfferingComponentFactory(
            offering=self.offering,
            billing_type=marketplace_models.OfferingComponent.BillingTypes.LIMIT,
        )
        self.plan = marketplace_factories.PlanFactory(
            offering=self.offering, unit_price=10
        )
        self.plan_component = marketplace_factories.PlanComponentFactory(
            plan=self.plan, component=self.offering_component
        )
        self.resource = marketplace_factories.ResourceFactory(
            name='test_request',
            project=self.project,
            offering=self.offering,
            plan=self.plan,
            limits=self.old_limits,
        )
        self.resource.set_state_ok()
        self.resource.save()

        self.success_issue_status = 'ok'
        support_factories.IssueStatusFactory(
            name=self.success_issue_status,
            type=support_models.IssueStatus.Types.RESOLVED,
        )

        self.error_issue_status = 'error'
        support_factories.IssueStatusFactory(
            name=self.error_issue_status, type=support_models.IssueStatus.Types.CANCELED
        )

    def test_when_issue_is_resolved_limits_are_updated(self):
        order_item = self.get_order_item(self.success_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.DONE)
        self.assertEqual(self.mock_get_active_backend().create_issue.call_count, 1)
        self.assertEqual(
            self.mock_get_active_backend().create_issue_links.call_count, 1
        )
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.state, marketplace_models.Resource.States.OK)
        self.assertEqual(self.resource.limits, self.new_limits)

    def test_fail_case(self):
        order_item = self.get_order_item(self.error_issue_status)
        self.assertEqual(order_item.state, marketplace_models.OrderItem.States.ERRED)
        self.resource.refresh_from_db()
        self.assertEqual(self.resource.state, marketplace_models.Resource.States.ERRED)
        self.assertEqual(self.resource.limits, self.old_limits)

    def test_description_formatting(self):
        issue = self.get_issue()
        resource = issue.resource.resource
        self.assertTrue('Update limits for resource' in issue.description)
        self.assertTrue('CPU: 10' in issue.description)
        self.assertTrue('CPU: 20' in issue.description)
        self.assertTrue(resource.uuid.hex in issue.description)

    def request_limit_update(self, user=None):
        user = user or self.user
        url = marketplace_factories.ResourceFactory.get_url(
            resource=self.resource, action='update_limits'
        )
        payload = {'limits': self.new_limits}

        self.client.force_authenticate(user)
        return self.client.post(url, payload)

    def get_issue(self):
        response = self.request_limit_update()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = marketplace_models.Order.objects.get(uuid=response.data['order_uuid'])
        order_item = order.items.first()
        test_utils.process_order_item(order_item, self.user)
        return get_order_item_issue(order_item)

    def get_order_item(self, issue_status):
        response = self.request_limit_update()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = marketplace_models.Order.objects.get(uuid=response.data['order_uuid'])
        order_item = order.items.first()
        test_utils.process_order_item(order_item, self.user)

        issue = get_order_item_issue(order_item)
        issue.status = issue_status
        issue.save()
        order_item.refresh_from_db()
        return order_item


@override_settings(task_always_eager=True)
class NotificationTest(BaseTest):
    def setUp(self):
        self.offering = marketplace_factories.OfferingFactory(
            type=PLUGIN_NAME, options={'order': []}
        )
        self.service_provider = marketplace_factories.ServiceProviderFactory(
            customer=self.offering.customer, lead_email='to@example.com'
        )

    def test_send_notification_if_request_base_item_and_backend_issue_have_been_created(
        self,
    ):
        self.create_issue()
        self.assertEqual(len(mail.outbox), 1)

    def test_do_not_send_notification_if_lead_email_is_not_set(self):
        self.service_provider.lead_email = ''
        self.service_provider.save()
        self.create_issue()

        self.assertEqual(len(mail.outbox), 0)

    def test_notification_body_and_subject(self):
        self.offering.name = 'Offering name'
        self.offering.save()
        self.service_provider.lead_body = (
            '{{order_item.order}}; attributes: {{order_item.attributes}}; '
            'plan: {{order_item.plan}}; '
            'contacts: {{order_item.order.project.customer.contact_details}}'
            'issue: {{issue.backend_id}}'
        )
        self.service_provider.lead_subject = '{{order_item.offering.name}}'
        self.service_provider.save()

        self.create_issue()
        self.assertEqual(mail.outbox[0].subject, self.offering.name)
        body = Template(self.service_provider.lead_body).render(
            Context(
                {'order_item': self.order_item, 'issue': self.issue}, autoescape=False
            )
        )
        self.assertEqual(mail.outbox[0].body, body)

    def test_attributes_with_display_names(self):
        self.offering.name = 'Offering name'
        self.offering.options = {
            'order': ['test_option'],
            'options': {
                'test_option': {
                    'type': 'string',
                    'label': 'display name',
                    'required': True,
                }
            },
        }
        self.offering.save()
        self.service_provider.lead_body = '{{order_item.attributes_with_display_names}}'
        self.service_provider.save()

        self.create_issue(attributes={'test_option': 'OK'})
        self.assertTrue('display name' in mail.outbox[0].body)

    def create_issue(self, **kwargs):
        resource = marketplace_factories.ResourceFactory()
        self.order_item = marketplace_factories.OrderItemFactory(
            resource=resource, offering=self.offering, **kwargs
        )
        self.issue = support_factories.IssueFactory(
            backend_id='', key='', resource=self.order_item
        )
        # Trigger handler
        self.issue.backend_id = 'TST-1'
        self.issue.save()


class IssueLogTest(test.APITransactionTestCase):
    def test_get_logger_scope_if_issue_resource_is_order_item(self):
        order_item = marketplace_factories.OrderItemFactory()
        issue = support_factories.IssueFactory()
        issue.resource = order_item
        issue.save()
        logger = IssueEventLogger
        scope = logger.get_scopes({'issue': issue})
        self.assertTrue(order_item.order.project in scope)
