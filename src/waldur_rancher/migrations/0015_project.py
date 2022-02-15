# Generated by Django 2.2.9 on 2020-02-27 12:58

import django.db.models.deletion
import django.utils.timezone
import model_utils.fields
from django.db import migrations, models

import waldur_core.core.fields
import waldur_core.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('waldur_rancher', '0014_drop_constraints'),
    ]

    operations = [
        migrations.CreateModel(
            name='Project',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'created',
                    model_utils.fields.AutoCreatedField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name='created',
                    ),
                ),
                (
                    'modified',
                    model_utils.fields.AutoLastModifiedField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name='modified',
                    ),
                ),
                (
                    'description',
                    models.CharField(
                        blank=True, max_length=500, verbose_name='description'
                    ),
                ),
                (
                    'name',
                    models.CharField(
                        max_length=150,
                        validators=[waldur_core.core.validators.validate_name],
                        verbose_name='name',
                    ),
                ),
                ('uuid', waldur_core.core.fields.UUIDField()),
                (
                    'runtime_state',
                    models.CharField(
                        blank=True, max_length=150, verbose_name='runtime state'
                    ),
                ),
                ('backend_id', models.CharField(blank=True, max_length=255)),
                (
                    'cluster',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to='waldur_rancher.Cluster',
                        related_name='+',
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
