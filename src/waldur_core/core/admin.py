import copy
import json
from collections import defaultdict

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import forms as admin_forms
from django.contrib.auth import admin as auth_admin
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.forms.utils import flatatt
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import re_path, reverse
from django.utils.functional import cached_property
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from jsoneditor.forms import JSONEditor
from rest_framework import permissions as rf_permissions
from rest_framework.exceptions import ParseError
from reversion.admin import VersionAdmin

from waldur_auth_social.utils import pull_remote_eduteams_user
from waldur_core.core import models
from waldur_core.core.authentication import can_access_admin_site


def get_admin_url(obj):
    return reverse(
        f'admin:{obj._meta.app_label}_{obj._meta.model_name}_change',
        args=[obj.id],
    )


def render_to_readonly(value):
    return f"<p>{value}</p>"


class ReadonlyTextWidget(forms.TextInput):
    def format_value(self, value):
        return value

    def render(self, name, value, attrs=None, renderer=None):
        return render_to_readonly(self.format_value(value))


class ReadOnlyAdminMixin:
    """
    Disables all editing capabilities.
    Please ensure that readonly_fields is specified in derived class.
    """

    change_form_template = 'admin/core/readonly_change_form.html'

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_staff:
            return True
        return False

    def save_model(self, request, obj, form, change):
        pass

    def delete_model(self, request, obj):
        pass

    def save_related(self, request, form, formsets, change):
        pass


class CopyButtonMixin:
    class Media:
        js = (settings.STATIC_URL + 'landing/js/copy2clipboard.js',)

    def render(self, name, value, attrs=None, renderer=None):
        result = super().render(name, value, attrs)
        button_attrs = {
            'class': 'button copy-button',
            'data-target-id': attrs['id'],
        }
        result += f"<a {flatatt(button_attrs)}>Copy</a>"
        return mark_safe(result)  # noqa: S308, S703


class PasswordWidget(CopyButtonMixin, forms.PasswordInput):
    template_name = 'admin/core/widgets/password-widget.html'

    def __init__(self, attrs=None):
        super().__init__(attrs, render_value=True)


class JsonWidget(CopyButtonMixin, JSONEditor):
    class Media:
        js = JSONEditor.Media.js + CopyButtonMixin.Media.js


def format_json_field(value):
    template = '<div><pre style="overflow: hidden">{0}</pre></div>'
    formatted_value = json.dumps(value, indent=True, ensure_ascii=False)
    return mark_safe(template.format(formatted_value))  # noqa: S308, S703


class OptionalChoiceField(forms.ChoiceField):
    def __init__(self, choices=(), *args, **kwargs):
        empty = [('', '---------')]
        choices = empty + sorted(choices, key=lambda pair: pair[1])
        super().__init__(choices=choices, *args, **kwargs)


class UserCreationForm(auth_forms.UserCreationForm):
    class Meta:
        model = get_user_model()
        fields = ("username",)

    # overwritten to support custom User model
    def clean_username(self):
        # Since User.username is unique, this check is redundant,
        # but it sets a nicer error message than the ORM. See #13147.
        username = self.cleaned_data["username"]
        try:
            get_user_model()._default_manager.get(username=username)
        except get_user_model().DoesNotExist:
            return username
        raise forms.ValidationError(
            _('Username is not unique.'),
            code='duplicate_username',
        )


class UserChangeForm(auth_forms.UserChangeForm):
    class Meta:
        model = get_user_model()
        exclude = ('details',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        competences = [
            (key, key) for key in settings.WALDUR_CORE.get('USER_COMPETENCE_LIST', [])
        ]
        self.fields['competence'] = OptionalChoiceField(
            choices=competences, required=False
        )

    def clean_civil_number(self):
        # Empty string should be converted to None.
        # Otherwise uniqueness constraint is violated.
        # See also: http://stackoverflow.com/a/1400046/175349
        civil_number = self.cleaned_data.get('civil_number')
        if civil_number:
            return civil_number.strip()
        return None


class ExcludedFieldsAdminMixin(admin.ModelAdmin):
    """
    This mixin allows to toggle display of fields in Django model admin according to custom logic.
    It's expected that inherited class has implemented excluded_fields property.
    """

    @cached_property
    def excluded_fields(self):
        return []

    def filter_excluded_fields(self, fields):
        return [field for field in fields if field not in self.excluded_fields]

    def exclude_fields_from_fieldset(self, fieldset):
        name, options = fieldset
        fields = options.get('fields', ())
        options = copy.copy(options)
        options['fields'] = self.filter_excluded_fields(fields)
        return (name, options)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        return self.filter_excluded_fields(fields)

    def get_list_display(self, request):
        fields = super().get_list_display(request)
        return self.filter_excluded_fields(fields)

    def get_search_fields(self, request):
        fields = super().get_search_fields(request)
        return self.filter_excluded_fields(fields)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        return list(map(self.exclude_fields_from_fieldset, fieldsets))


class NativeNameAdminMixin(ExcludedFieldsAdminMixin):
    @cached_property
    def excluded_fields(self):
        if not settings.WALDUR_CORE['NATIVE_NAME_ENABLED']:
            return ['native_name']
        return []


class UserAdmin(NativeNameAdminMixin, auth_admin.UserAdmin, VersionAdmin):
    list_display = (
        'username',
        'uuid',
        'email',
        'first_name',
        'last_name',
        'native_name',
        'is_active',
        'is_staff',
        'is_support',
        'is_identity_manager',
    )
    search_fields = (
        'username',
        'uuid',
        'first_name',
        'last_name',
        'native_name',
        'email',
        'civil_number',
    )
    list_filter = ('is_active', 'is_staff', 'is_support', 'registration_method')
    date_hierarchy = 'date_joined'
    fieldsets = (
        (None, {'fields': ('username', 'password', 'registration_method', 'uuid')}),
        (
            _('Personal info'),
            {
                'fields': (
                    'civil_number',
                    'first_name',
                    'last_name',
                    'native_name',
                    'email',
                    'preferred_language',
                    'competence',
                    'phone_number',
                )
            },
        ),
        (
            _('Image'),
            {'fields': ('image',)},
        ),
        (_('Organization'), {'fields': ('organization', 'job_title', 'affiliations')}),
        (
            _('Permissions'),
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_support',
                    'is_identity_manager',
                    'customer_roles',
                    'project_roles',
                    'notifications_enabled',
                )
            },
        ),
        (
            _('Important dates'),
            {'fields': ('last_login', 'date_joined', 'agreement_date', 'last_sync')},
        ),
        (
            _('Authentication backend details'),
            {'fields': ('format_details', 'backend_id')},
        ),
    )
    readonly_fields = (
        'registration_method',
        'affiliations',
        'agreement_date',
        'customer_roles',
        'project_roles',
        'uuid',
        'last_login',
        'last_sync',
        'date_joined',
        'format_details',
    )
    form = UserChangeForm
    add_form = UserCreationForm

    def customer_roles(self, instance):
        from waldur_core.structure.models import CustomerPermission

        permissions = CustomerPermission.objects.filter(
            user=instance, is_active=True
        ).order_by('customer')

        return format_html_join(
            mark_safe('<br/>'),  # noqa: S308
            '<a href={}>{}</a>',
            (
                (get_admin_url(permission.customer), str(permission))
                for permission in permissions
            ),
        ) or mark_safe(  # noqa: S308, S703
            "<span class='errors'>%s</span>"
            % _('User has no roles in any organization.')
        )

    customer_roles.short_description = _('Roles in organizations')

    def project_roles(self, instance):
        from waldur_core.structure.models import ProjectPermission

        permissions = ProjectPermission.objects.filter(
            user=instance, is_active=True
        ).order_by('project')

        return format_html_join(
            mark_safe('<br/>'),  # noqa: S308
            '<a href={}>{}</a>',
            (
                (get_admin_url(permission.project), str(permission))
                for permission in permissions
            ),
        ) or mark_safe(  # noqa: S308, S703
            "<span class='errors'>%s</span>" % _('User has no roles in any project.')
        )

    project_roles.short_description = _('Roles in projects')

    def format_details(self, obj):
        return format_json_field(obj.details)

    format_details.allow_tags = True
    format_details.short_description = _('Details')

    actions = ['pull_remote_user']

    def pull_remote_user(self, request, queryset):
        if not settings.WALDUR_AUTH_SOCIAL['REMOTE_EDUTEAMS_ENABLED']:
            messages.error(
                request,
                _('Remote eduTEAMS account synchronization extension is disabled.'),
            )
            return
        for remote_user in queryset:
            if remote_user.registration_method == 'eduteams':
                try:
                    pull_remote_eduteams_user(remote_user.username)
                except ParseError:
                    messages.error(
                        request,
                        _('Unable to pull remote eduTEAMS account %s.')
                        % remote_user.username,
                    )

    pull_remote_user.short_description = 'Pull remote eduTEAMS users'


class SshPublicKeyAdmin(VersionAdmin):
    list_display = ('user', 'name', 'fingerprint')
    search_fields = ('user__username', 'name', 'fingerprint')


class ChangeEmailRequestAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('user', 'email', 'created')


class CustomAdminAuthenticationForm(admin_forms.AdminAuthenticationForm):
    error_messages = {
        'invalid_login': _(
            "Please enter the correct %(username)s and password "
            "for a staff or a support account. Note that both fields may be "
            "case-sensitive."
        ),
    }

    def confirm_login_allowed(self, user):
        if not can_access_admin_site(user):
            return super().confirm_login_allowed(user)


class CustomAdminSite(admin.AdminSite):
    site_title = _('Waldur MasterMind admin')
    site_header = _('Waldur MasterMind administration')
    index_title = _('Waldur MasterMind administration')
    login_form = CustomAdminAuthenticationForm

    def has_permission(self, request):
        is_safe = request.method in rf_permissions.SAFE_METHODS
        return can_access_admin_site(request.user) and (
            is_safe or request.user.is_staff
        )

    @classmethod
    def clone_default(cls):
        instance = cls()
        instance._registry = admin.site._registry.copy()
        instance._actions = admin.site._actions.copy()
        instance._global_actions = admin.site._global_actions.copy()
        return instance


admin_site = CustomAdminSite.clone_default()
admin.site = admin_site
admin.site.register(models.User, UserAdmin)
admin.site.register(models.SshPublicKey, SshPublicKeyAdmin)
admin.site.register(models.ChangeEmailRequest, ChangeEmailRequestAdmin)


# TODO: Extract common classes to admin_utils module and remove hack.
# This hack is needed because admin is imported several times.
# Please note that admin module should NOT be imported by other apps.
if admin.site.is_registered(Group):
    admin.site.unregister(Group)


class ReversionAdmin(VersionAdmin):
    def add_view(self, request, form_url='', extra_context=None):
        # Revision creation is ignored in this method because it has to be implemented in model.save method
        return super(VersionAdmin, self).add_view(request, form_url, extra_context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        # Revision creation is ignored in this method because it has to be implemented in model.save method
        return super(VersionAdmin, self).change_view(
            request, object_id, form_url, extra_context
        )


class ExecutorAdminAction:
    """Add executor as action to admin model.

    Usage example:
        class PullSecurityGroups(ExecutorAdminAction):
            executor = executors.TenantPullSecurityGroupsExecutor  # define executor
            short_description = 'Pull security groups'  # description for admin page
            confirmation = True # if your action requires a confirmation else set False

            def validate(self, tenant):
                if tenant.state != Tenant.States.OK:
                    raise ValidationError('Tenant has to be in state OK to pull security groups.')

        pull_security_groups = PullSecurityGroups()  # this action could be registered as admin action

    """

    executor = NotImplemented
    short_description = ''
    confirmation_template = 'admin/action_confirmation.html'
    confirmation_description = ''
    confirmation = False

    def __call__(self, admin_class, request, queryset):
        if self.confirmation and not request.POST.get('confirmed'):
            return self.confirmation_response(admin_class, request, queryset)
        else:
            return self.execute(admin_class, request, queryset)

    def confirmation_response(self, admin_class, request, queryset):
        opts = admin_class.model._meta
        app_label = opts.app_label
        object_name = str(opts.verbose_name)
        context = {
            **admin_class.admin_site.each_context(request),
            'title': _("Are you sure?"),
            'object_name': object_name,
            'queryset': queryset,
            'opts': opts,
            'app_label': app_label,
            'action_name': self.get_action_name(admin_class),
            'confirmation_description': self.confirmation_description,
            'description': self.short_description,
        }
        request.current_app = admin_class.admin_site.name
        context.update(
            media=admin_class.media,
        )
        return TemplateResponse(
            request,
            self.confirmation_template,
            context,
        )

    def execute(self, admin_class, request, queryset):
        errors = defaultdict(list)
        successfully_executed = []
        for instance in queryset:
            try:
                self.validate(instance)
            except ValidationError as e:
                errors[str(e)].append(instance)
            else:
                params = self.get_execute_params(request, instance)
                self.executor.execute(instance, **params)
                successfully_executed.append(instance)

        if successfully_executed:
            message = _(
                'Operation was successfully scheduled for %(count)d instances: %(names)s'
            ) % dict(
                count=len(successfully_executed),
                names=', '.join([str(i) for i in successfully_executed]),
            )
            admin_class.message_user(request, message)

        for error, instances in errors.items():
            message = _(
                'Failed to schedule operation for %(count)d instances: %(names)s. Error: %(message)s'
            ) % dict(
                count=len(instances),
                names=', '.join([str(i) for i in instances]),
                message=error,
            )
            admin_class.message_user(request, message, level=messages.ERROR)

    def get_action_name(self, admin_class):
        for action_name in admin_class.actions:
            action_obj = getattr(admin_class, action_name, None)
            if isinstance(action_obj, self.__class__) and action_obj.confirmation:
                return action_name

    def validate(self, instance):
        """Raise validation error if action cannot be performed for given instance"""
        pass

    def get_execute_params(self, request, instance):
        """Returns additional parameters for the executor"""
        return {}


class ExtraActionsMixin:
    """
    Allows to add extra actions to admin list page.
    """

    change_list_template = 'admin/core/change_list.html'

    def get_extra_actions(self):
        raise NotImplementedError(
            'Method "get_extra_actions" should be implemented in ExtraActionsMixin.'
        )

    def get_urls(self):
        """
        Inject extra action URLs.
        """
        urls = []

        for action in self.get_extra_actions():
            regex = fr'^{self._get_action_href(action)}/$'
            view = self.admin_site.admin_view(action)
            urls.append(re_path(regex, view))

        return urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        """
        Inject extra links into template context.
        """
        links = []

        for action in self.get_extra_actions():
            links.append(
                {
                    'label': self._get_action_label(action),
                    'href': self._get_action_href(action),
                }
            )

        extra_context = extra_context or {}
        extra_context['extra_links'] = links

        return super().changelist_view(
            request,
            extra_context=extra_context,
        )

    def _get_action_href(self, action):
        return action.__name__

    def _get_action_label(self, action):
        return getattr(action, 'name', action.__name__.replace('_', ' ').capitalize())


class ExtraActionsObjectMixin:
    """
    Allows to add extra actions to admin object edit page.
    """

    change_form_template = 'admin/core/change_form.html'

    def get_extra_object_actions(self):
        raise NotImplementedError(
            'Method "get_extra_object_actions" should be implemented in ExtraActionsMixin.'
        )

    def get_urls(self):
        """
        Inject extra action URLs.
        """
        urls = []

        for action in self.get_extra_object_actions():
            regex = fr'^(.+)/change/{self._get_action_href(action)}/$'
            view = self.admin_site.admin_view(action)
            urls.append(re_path(regex, view))

        return urls + super().get_urls()

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        Inject extra links into template context.
        """
        links = []
        obj = get_object_or_404(self.model, pk=object_id)

        for action in self.get_extra_object_actions():
            validator = self._get_action_validator(action)
            links.append(
                {
                    'label': self._get_action_label(action),
                    'href': self._get_action_href(action),
                    'show': True if not validator else validator(request, obj),
                }
            )

        extra_context = extra_context or {}
        extra_context['extra_object_links'] = links

        return super().change_view(
            request,
            object_id,
            form_url,
            extra_context=extra_context,
        )

    def _get_action_href(self, action):
        return action.__name__

    def _get_action_label(self, action):
        return getattr(action, 'name', action.__name__.replace('_', ' ').capitalize())

    def _get_action_validator(self, action):
        return getattr(action, 'validator', None)


class UpdateOnlyModelAdmin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_staff:
            return True
        return False


class HideAdminOriginalMixin(admin.ModelAdmin):
    class Media:
        css = {
            'all': (settings.STATIC_URL + "waldur_core/css/hide_admin_original.css",)
        }
