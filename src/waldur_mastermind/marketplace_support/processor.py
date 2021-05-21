from waldur_mastermind.marketplace import processors
from waldur_mastermind.marketplace_support import utils
from waldur_mastermind.support import models as support_models

from .views import IssueViewSet


class CreateRequestProcessor(processors.BaseCreateResourceProcessor):
    viewset = IssueViewSet

    def get_post_data(self):
        return {'uuid': str(self.order_item.uuid)}

    def process_order_item(self, user):
        super(CreateRequestProcessor, self).process_order_item(user)
        try:
            issue = support_models.Issue.objects.get(
                resource_object_id=self.order_item.id
            )
            self.order_item.resource.backend_id = issue.backend_id or ''
            self.order_item.resource.save(update_fields=['backend_id'])
        except support_models.Issue.DoesNotExist:
            pass

    @classmethod
    def get_resource_model(cls):
        return support_models.Issue


class DeleteRequestProcessor(processors.DeleteScopedResourceProcessor):
    viewset = IssueViewSet

    def get_resource(self):
        return self.order_item


class UpdateRequestProcessor(processors.UpdateScopedResourceProcessor):
    def get_view(self):
        return IssueViewSet.as_view({'post': 'update'})

    def get_post_data(self):
        return {'uuid': str(self.order_item.uuid)}

    def get_resource(self):
        return self.order_item.resource

    def update_limits_process(self, user):
        utils.create_issue(
            self.order_item,
            description=utils.format_update_limits_description(self.order_item),
            summary='Request to update limits for %s' % self.order_item.resource.name,
        )
        return False
