from django.conf import settings
from django.db import models
from rest_framework import serializers

from waldur_core.media.utils import encode_protected_url, s3_to_waldur_media_url
from waldur_core.structure.metadata import merge_dictionaries


class ProtectedFileMixin:
    def to_representation(self, value):
        if not value:
            return None

        if not settings.USE_PROTECTED_URL:
            url = super(ProtectedFileMixin, self).to_representation(value)
            if (
                settings.CONVERT_MEDIA_URLS_TO_MASTERMIND_NETLOC
            ):  # If using s3-compatible storage
                url = s3_to_waldur_media_url(url, self.context['request'])
            return url

        return encode_protected_url(
            value.instance, field=self.source_attrs[-1], request=self.context['request']
        )


class ProtectedFileField(ProtectedFileMixin, serializers.FileField):
    pass


class ProtectedImageField(ProtectedFileMixin, serializers.ImageField):
    pass


class ProtectedMediaSerializerMixin(serializers.ModelSerializer):
    serializer_field_mapping = merge_dictionaries(
        serializers.ModelSerializer.serializer_field_mapping,
        {models.FileField: ProtectedFileField, models.ImageField: ProtectedImageField,},
    )
