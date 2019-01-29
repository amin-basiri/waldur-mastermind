from django.utils.translation import ugettext_lazy as _
from rest_framework import decorators, viewsets, \
    response, status, serializers as rf_serializers

from waldur_core.core import validators as core_validators
from waldur_core.structure import views as structure_views

from . import models, serializers, executors, filters


class AzureServiceViewSet(structure_views.BaseServiceViewSet):
    queryset = models.AzureService.objects.all()
    serializer_class = serializers.ServiceSerializer


class AzureServiceProjectLinkViewSet(structure_views.BaseServiceProjectLinkViewSet):
    queryset = models.AzureServiceProjectLink.objects.all()
    serializer_class = serializers.ServiceProjectLinkSerializer


class ImageViewSet(structure_views.BaseServicePropertyViewSet):
    queryset = models.Image.objects.all()
    serializer_class = serializers.ImageSerializer
    filter_class = filters.ImageFilter
    lookup_field = 'uuid'

    def get_queryset(self):
        return models.Image.objects.order_by('name')


class SizeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Size.objects.all()
    serializer_class = serializers.SizeSerializer
    lookup_field = 'uuid'


class LocationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.Location.objects.all()
    serializer_class = serializers.LocationSerializer
    lookup_field = 'uuid'


class ResourceGroupViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.ResourceGroup.objects.all()
    serializer_class = serializers.ResourceGroupSerializer
    lookup_field = 'uuid'


class VirtualMachineViewSet(structure_views.BaseResourceViewSet):
    queryset = models.VirtualMachine.objects.all()
    filter_class = filters.VirtualMachineFilter
    serializer_class = serializers.VirtualMachineSerializer
    create_executor = executors.VirtualMachineCreateExecutor
    delete_executor = executors.VirtualMachineDeleteExecutor

    @decorators.detail_route(methods=['post'])
    def start(self, request, uuid=None):
        virtual_machine = self.get_object()
        executors.VirtualMachineStartExecutor().execute(virtual_machine)
        return response.Response({'status': _('start was scheduled')}, status=status.HTTP_202_ACCEPTED)

    start_validators = [core_validators.StateValidator(models.VirtualMachine.States.OK),
                        core_validators.RuntimeStateValidator('stopped')]
    start_serializer_class = rf_serializers.Serializer

    @decorators.detail_route(methods=['post'])
    def stop(self, request, uuid=None):
        virtual_machine = self.get_object()
        executors.VirtualMachineStopExecutor().execute(virtual_machine)
        return response.Response({'status': _('stop was scheduled')}, status=status.HTTP_202_ACCEPTED)

    stop_validators = [core_validators.StateValidator(models.VirtualMachine.States.OK),
                       core_validators.RuntimeStateValidator('running')]
    stop_serializer_class = rf_serializers.Serializer

    @decorators.detail_route(methods=['post'])
    def restart(self, request, uuid=None):
        virtual_machine = self.get_object()
        executors.VirtualMachineRestartExecutor().execute(virtual_machine)
        return response.Response({'status': _('restart was scheduled')}, status=status.HTTP_202_ACCEPTED)

    restart_validators = [core_validators.StateValidator(models.VirtualMachine.States.OK),
                          core_validators.RuntimeStateValidator('running')]
    restart_serializer_class = rf_serializers.Serializer
