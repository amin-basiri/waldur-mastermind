from unittest import mock
from urllib.parse import urlencode
from uuid import uuid4

import responses
from django.test import override_settings
from rest_framework import status, test

from waldur_core.core.tests.helpers import override_waldur_core_settings
from waldur_core.structure.tests.factories import UserFactory
from waldur_mastermind.marketplace import models
from waldur_mastermind.marketplace import models as marketplace_models
from waldur_mastermind.marketplace.tests import factories
from waldur_mastermind.marketplace.tests import factories as marketplace_factories
from waldur_mastermind.marketplace.tests import fixtures
from waldur_mastermind.marketplace.tests.factories import OfferingFactory
from waldur_mastermind.marketplace_remote.processors import (
    RemoteCreateResourceProcessor,
)
from waldur_mastermind.marketplace_remote.tasks import OfferingPullTask

from .. import PLUGIN_NAME


class RemoteCustomersTest(test.APITransactionTestCase):
    @responses.activate
    def test_remote_customers_are_listed_for_given_token_and_api_url(self):
        responses.add(responses.GET, 'https://remote-waldur.com/customers/', json=[])
        self.client.force_login(UserFactory())
        response = self.client.post(
            '/api/remote-waldur-api/remote_customers/',
            {
                'api_url': 'https://remote-waldur.com/',
                'token': 'valid_token',
            },
        )
        self.assertEqual(
            responses.calls[0].request.headers['Authorization'], 'token valid_token'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])


class OfferingComponentPullTest(test.APITransactionTestCase):
    def setUp(self) -> None:
        fixture = fixtures.MarketplaceFixture()
        self.offering = fixture.offering
        self.plan: models.Plan = fixture.plan
        self.plan_component: models.PlanComponent = fixture.plan_component
        self.component = fixture.offering_component
        self.offering.backend_id = 'offering-backend-id'
        self.offering.secret_options = {
            'api_url': 'https://remote-waldur.com/',
            'token': '123',
            'customer_uuid': '456',
        }
        self.task = OfferingPullTask()
        self.remote_plan_uuid = uuid4().hex
        self.plan.backend_id = self.remote_plan_uuid
        self.plan.save()

        self.remote_offering = {
            'name': self.offering.name,
            'description': self.offering.description,
            'full_description': self.offering.full_description,
            'terms_of_service': self.offering.terms_of_service,
            'options': self.offering.options,
            'thumbnail': None,
            'components': [
                {
                    'name': self.component.name,
                    'type': self.component.type,
                    'description': self.component.description,
                    'article_code': self.component.article_code,
                    'measured_unit': self.component.measured_unit,
                    'billing_type': self.component.billing_type,
                    'min_value': self.component.min_value,
                    'max_value': self.component.max_value,
                    'is_boolean': self.component.is_boolean,
                    'default_limit': self.component.default_limit,
                    'limit_period': self.component.limit_period,
                    'limit_amount': self.component.limit_amount,
                }
            ],
            "plans": [
                {
                    "uuid": self.remote_plan_uuid,
                    "name": self.plan.name,
                    "description": self.plan.description,
                    "article_code": self.plan.article_code,
                    "prices": {self.component.type: float(self.plan_component.price)},
                    "quotas": {self.component.type: self.plan_component.amount},
                    "max_amount": self.plan.max_amount,
                    "archived": False,
                    "is_active": True,
                    "unit_price": self.plan.unit_price,
                    "unit": self.plan.unit,
                }
            ],
        }

    def tearDown(self) -> None:
        responses.reset()
        return super().tearDown()

    @responses.activate
    @override_settings(task_always_eager=True)
    def test_update_component(self):
        new_billing_type = 'usage'
        self.remote_offering['components'][0]['billing_type'] = new_billing_type
        responses.add(
            responses.GET,
            f'https://remote-waldur.com/marketplace-public-offerings/{self.offering.backend_id}/',
            json=self.remote_offering,
        )
        self.task.pull(self.offering)
        self.component.refresh_from_db()
        self.assertEqual(new_billing_type, self.component.billing_type)
        self.assertEqual(1, self.offering.components.count())

    @responses.activate
    @override_settings(task_always_eager=True)
    def test_stale_and_new_components(self):
        new_type = 'gpu'
        self.remote_offering['components'][0]['type'] = new_type
        self.remote_offering['plans'][0]['prices'] = {
            new_type: float(self.plan_component.price)
        }
        self.remote_offering['plans'][0]['quotas'] = {
            new_type: self.plan_component.amount
        }
        responses.add(
            responses.GET,
            f'https://remote-waldur.com/marketplace-public-offerings/{self.offering.backend_id}/',
            json=self.remote_offering,
        )

        self.task.pull(self.offering)

        self.assertEqual(1, self.offering.components.count())
        new_component = self.offering.components.first()
        self.assertEqual(new_type, new_component.type)
        self.assertEqual(
            0, models.OfferingComponent.objects.filter(type=self.component.type).count()
        )
        self.plan.refresh_from_db()
        self.assertEqual(
            0, models.PlanComponent.objects.filter(pk=self.plan_component.pk).count()
        )
        self.assertEqual(
            1, self.plan.components.filter(component=new_component).count()
        )

    @responses.activate
    @override_settings(task_always_eager=True)
    def test_update_plan(self):
        new_plan_name = 'New plan'
        plan_component_new_price = 50.0
        new_plan_component_price = 100.0
        new_plan_component_amount = 1000
        new_component_type = 'additional'
        new_component_data = self.remote_offering['components'][0].copy()
        new_component_data['type'] = new_component_type

        self.remote_offering['plans'][0]['name'] = new_plan_name
        self.remote_offering['plans'][0]['prices'][
            self.component.type
        ] = plan_component_new_price

        self.remote_offering['components'].append(new_component_data)
        self.remote_offering['plans'][0]['prices'][
            new_component_type
        ] = new_plan_component_price
        self.remote_offering['plans'][0]['quotas'][
            new_component_type
        ] = new_plan_component_amount

        responses.add(
            responses.GET,
            f'https://remote-waldur.com/marketplace-public-offerings/{self.offering.backend_id}/',
            json=self.remote_offering,
        )

        self.task.pull(self.offering)

        self.offering.refresh_from_db()
        self.assertEqual(1, self.offering.plans.count())

        self.plan.refresh_from_db()
        self.assertEqual(new_plan_name, self.plan.name)
        self.assertEqual(2, self.plan.components.count())
        self.assertEqual(self.remote_plan_uuid, self.plan.backend_id)

        self.plan_component.refresh_from_db()
        self.assertEqual(plan_component_new_price, self.plan_component.price)

        new_plan_component = self.plan.components.all()[1]
        self.assertEqual(new_component_type, new_plan_component.component.type)
        self.assertEqual(new_plan_component_price, new_plan_component.price)
        self.assertEqual(new_plan_component_amount, new_plan_component.amount)

    @responses.activate
    @override_settings(task_always_eager=True)
    def test_stale_and_new_plan(self):
        new_plan_uuid = uuid4().hex
        remote_plan = self.remote_offering['plans'][0]
        remote_plan['uuid'] = new_plan_uuid
        responses.add(
            responses.GET,
            f'https://remote-waldur.com/marketplace-public-offerings/{self.offering.backend_id}/',
            json=self.remote_offering,
        )

        self.task.pull(self.offering)

        self.assertEqual(1, models.Plan.objects.filter(pk=self.plan.pk).count())

        self.offering.refresh_from_db()

        self.assertEqual(2, self.offering.plans.count())
        old_plan = self.offering.plans.get(backend_id=self.remote_plan_uuid)
        self.assertTrue(old_plan.archived)

        new_plan = self.offering.plans.get(backend_id=new_plan_uuid)

        self.assertEqual(1, new_plan.components.count())

        new_plan_component = new_plan.components.first()
        self.assertEqual(self.component, new_plan_component.component)


class OfferingUpdateTest(test.APITransactionTestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.MarketplaceFixture()
        self.offering = self.fixture.offering
        self.offering.type = PLUGIN_NAME
        self.offering.save()
        self.url = factories.OfferingFactory.get_url(self.offering)

    def test_edit_of_fields_that_are_being_pulled_from_remote_waldur_is_not_available(
        self,
    ):
        old_name = self.offering.name
        self.client.force_authenticate(user=self.fixture.staff)
        response = self.client.patch(self.url, {'name': 'new_name'})
        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.name, old_name)

    def test_edit_of_offering_component_is_not_available(self):
        component_type = self.offering.components.filter().first().type
        self.client.force_authenticate(user=self.fixture.staff)
        response = self.client.patch(
            self.url,
            {
                "components": [
                    {
                        "billing_type": "limit",
                        "type": "test",
                        "name": "Test",
                        "description": "",
                        "measured_unit": "",
                        "limit_period": None,
                        "limit_amount": None,
                        "article_code": "",
                        "max_value": 500,
                        "min_value": 1,
                        "is_boolean": False,
                        "default_limit": None,
                        "factor": 1,
                    }
                ]
            },
        )
        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.offering.refresh_from_db()
        self.assertFalse(self.offering.components.filter(type='test').exists())
        self.assertTrue(self.offering.components.filter(type=component_type).exists())

    def test_edit_of_plans_is_not_available(self):
        self.client.force_authenticate(user=self.fixture.staff)
        plan = self.fixture.plan
        old_name = plan.name
        response = self.client.patch(
            self.url,
            {
                "plans": [
                    {
                        "url": factories.PlanFactory.get_public_url(plan),
                        "uuid": plan.uuid.hex,
                        "name": "new_name",
                        "max_amount": 100,
                    }
                ]
            },
        )
        self.assertEqual(status.HTTP_200_OK, response.status_code)
        plan.refresh_from_db()
        self.assertEqual(plan.max_amount, 100)
        self.assertEqual(plan.name, old_name)


@override_waldur_core_settings(MASTERMIND_URL='http://localhost')
class OfferingRemoteVersionTest(test.APITransactionTestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.MarketplaceFixture()
        self.offering = self.fixture.offering
        self.offering.type = PLUGIN_NAME
        self.offering.save()

        self.get_request_mock_patcher = mock.patch('waldur_client.requests.get')
        self.get_request_mock = self.get_request_mock_patcher.start()
        self.get_request_mock.side_effect = lambda url, **kwargs: self.client.get(
            url + '?' + urlencode(kwargs.get('params', {})), **kwargs
        )

        self.post_request_mock_patcher = mock.patch('waldur_client.requests.post')
        self.post_request_mock = self.post_request_mock_patcher.start()

        def post_request_mock(url, **kwargs):
            response = self.client.post(url, kwargs['json'])
            response.text = response.content
            return response

        self.post_request_mock.side_effect = post_request_mock

    def test_creating_remote_order_item(self):
        self.client.force_authenticate(user=self.fixture.staff)

        remote_offering = OfferingFactory(
            state=marketplace_models.Offering.States.ACTIVE
        )
        self.offering.secret_options = {
            'token': '0b67edfecdda37fe4b6e7d6c3e6360acb3a1f2bf',
            'api_url': 'http://localhost/api/',
            'customer_uuid': remote_offering.customer.uuid.hex,
            'service_provider_can_create_offering_user': False,
        }
        self.offering.backend_id = remote_offering.uuid.hex
        self.offering.save()

        order_item = marketplace_factories.OrderItemFactory(
            order=marketplace_factories.OrderFactory(project=self.fixture.project),
            offering=self.offering,
            attributes={'name': 'item_name', 'description': 'Description'},
            plan=self.fixture.plan,
        )

        processor = RemoteCreateResourceProcessor(order_item)
        processor.process_order_item(self.fixture.staff)

        order_item.refresh_from_db()
        self.assertTrue(order_item.backend_id)
