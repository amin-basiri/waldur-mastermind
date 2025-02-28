import threading

from django.utils.deprecation import MiddlewareMixin

from waldur_core.core import utils as core_utils

_locals = threading.local()


def get_event_context():
    return getattr(_locals, 'context', None)


def set_event_context(context):
    _locals.context = context


def reset_event_context():
    if hasattr(_locals, 'context'):
        del _locals.context


def set_current_user(user):
    context = get_event_context() or {}
    context.update(user._get_log_context('user'))
    set_event_context(context)


class CaptureEventContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        ip_address = core_utils.get_ip_address(request)
        if not ip_address:
            return
        context = {'ip_address': ip_address}

        user = getattr(request, 'user', None)
        if user and not user.is_anonymous:
            context.update(user._get_log_context('user'))

        set_event_context(context)

    def process_response(self, request, response):
        reset_event_context()
        return response
