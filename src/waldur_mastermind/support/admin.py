from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from waldur_core.core import admin as core_admin
from waldur_core.structure import admin as structure_admin

from . import backend, models, tasks
from .backend.basic import BasicBackend

User = get_user_model()


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return f'{user.full_name} - {user.username}'


class SupportUserAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'] = UserChoiceField(
            queryset=User.objects.all().order_by('first_name', 'last_name')
        )


class SupportUserAdmin(admin.ModelAdmin):
    list_display = ('user', 'backend_id', 'is_active', 'backend_name')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    form = SupportUserAdminForm


class SupportCustomerAdmin(admin.ModelAdmin):
    list_display = ('user', 'backend_id')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')


class IssueAdmin(core_admin.ExtraActionsObjectMixin, structure_admin.BackendModelAdmin):
    exclude = ('resource_content_type', 'resource_object_id')
    ordering = ('-created',)
    search_fields = ('key', 'backend_id', 'summary')
    list_filter = ('type', 'status', 'resolution')
    list_display = (
        'key',
        'summary',
        'type',
        'status',
        'resolution',
        'get_caller_full_name',
        'remote_id',
    )
    readonly_fields = ('resource',)

    def resource(self, obj):
        return obj.resource

    resource.short_description = 'Resource'

    def get_caller_full_name(self, obj):
        if obj.caller:
            return obj.caller.full_name
        return

    get_caller_full_name.short_description = 'Caller name'

    def resolve(self, request, pk=None):
        issue = get_object_or_404(models.Issue, pk=pk)
        issue.set_resolved()
        message = _('Issue has been resolved.')
        self.message_user(request, message)
        return HttpResponseRedirect('../')

    def cancel(self, request, pk=None):
        issue = get_object_or_404(models.Issue, pk=pk)
        issue.set_canceled()
        message = _('Issue has been canceled.')
        self.message_user(request, message)
        return HttpResponseRedirect('../')

    def buttons_validate(request, obj):
        if (
            isinstance(backend.get_active_backend(), BasicBackend)
            and obj.resolved is None
        ):
            return True

    resolve.validator = buttons_validate
    cancel.validator = buttons_validate

    def get_extra_object_actions(self):
        return [
            self.resolve,
            self.cancel,
        ]


class TemplateAttachmentInline(admin.TabularInline):
    model = models.TemplateAttachment
    fields = ('name', 'file')


class TemplateAdmin(core_admin.ExcludedFieldsAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'issue_type', 'created')
    search_fields = ('name', 'native_name')
    fields = (
        'name',
        'native_name',
        'description',
        'native_description',
        'issue_type',
        'created',
        'modified',
    )
    readonly_fields = ('created', 'modified')
    inlines = [TemplateAttachmentInline]

    @cached_property
    def excluded_fields(self):
        if not settings.WALDUR_CORE['NATIVE_NAME_ENABLED']:
            return ['native_name', 'native_description']
        return []


class RequestTypeAdmin(core_admin.ExtraActionsMixin, admin.ModelAdmin):
    list_display = ('name', 'issue_type_name', 'backend_id')
    search_fields = ('name',)

    def get_extra_actions(self):
        return [
            self.sync_request_types,
        ]

    def sync_request_types(self, request):
        tasks.sync_request_types.delay()
        self.message_user(
            request, _('Request types\' synchronization has been scheduled.')
        )
        return redirect(reverse('admin:support_requesttype_changelist'))


class PriorityAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'backend_id')
    search_fields = ('name', 'description')


class IssueStatusAdmin(admin.ModelAdmin):
    list_display = ('name', 'type')


class CommentAdmin(structure_admin.BackendModelAdmin):
    list_display = ('get_issue_key', 'is_public', 'author', 'created', 'remote_id')
    list_filter = ('is_public', 'author')
    search_fields = ('description',)

    def get_issue_key(self, obj):
        return f"{obj.issue.key}: {obj.issue.summary}"

    get_issue_key.short_description = 'Issue'


class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('issue', 'evaluation', 'state')
    list_filter = ('evaluation', 'state')
    search_fields = ('issue__key', 'issue__summary')


admin.site.register(models.Issue, IssueAdmin)
admin.site.register(models.Comment, CommentAdmin)
admin.site.register(models.Attachment)
admin.site.register(models.SupportUser, SupportUserAdmin)
admin.site.register(models.SupportCustomer, SupportCustomerAdmin)
admin.site.register(models.Template, TemplateAdmin)
admin.site.register(models.TemplateStatusNotification)
admin.site.register(models.IgnoredIssueStatus)
admin.site.register(models.RequestType, RequestTypeAdmin)
admin.site.register(models.Priority, PriorityAdmin)
admin.site.register(models.IssueStatus, IssueStatusAdmin)
admin.site.register(models.TemplateConfirmationComment)
admin.site.register(models.Feedback, FeedbackAdmin)
