import datetime

from django.conf import settings
from django.utils.timezone import now

from waldur_zammad.backend import ZammadBackend, ZammadBackendError

from . import SupportBackend


class ZammadServiceBackend(SupportBackend):
    def __init__(self):
        self.manager = ZammadBackend()

    def comment_destroy_is_available(self, comment):
        if now() - comment.created < datetime.timedelta(
            minutes=settings.WALDUR_ZAMMAD['COMMENT_COOLDOWN_DURATION']
        ):
            return True

    def comment_update_is_available(self, comment=None):
        return False

    def create_issue(self, issue):
        try:
            issue.begin_creating()
            issue.save()
            zammad_issue = self.manager.add_issue(
                issue.summary, issue.description, issue.caller.email
            )
            issue.backend_id = zammad_issue.id
            issue.status = zammad_issue.status
            issue.set_ok()
            issue.save()
            return zammad_issue
        except ZammadBackendError as e:
            issue.set_erred()
            issue.error_message = e
            issue.save()

    def update_waldur_issue_from_zammad(self, issue):
        zammad_issue = self.manager.get_issue(issue.backend_id)
        issue.status = zammad_issue.status
        issue.summary = zammad_issue.summary
        return issue.save()

    def create_confirmation_comment(self, issue):
        pass

    def create_comment(self, comment):
        pass
