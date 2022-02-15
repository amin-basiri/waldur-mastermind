# Generated by Django 2.2.10 on 2020-05-12 04:42

import django.db.models.deletion
from django.db import migrations, models

import waldur_core.core.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataciteReferral',
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
                ('uuid', waldur_core.core.fields.UUIDField()),
                ('object_id', models.PositiveIntegerField(blank=True, null=True)),
                ('pid', models.CharField(blank=True, max_length=255)),
                ('relation_type', models.CharField(blank=True, max_length=255)),
                ('resource_type', models.CharField(blank=True, max_length=255)),
                ('creator', models.CharField(blank=True, max_length=255)),
                ('publisher', models.CharField(blank=True, max_length=255)),
                ('title', models.CharField(blank=True, max_length=255)),
                ('published', models.CharField(blank=True, max_length=255)),
                ('referral_url', models.CharField(blank=True, max_length=255)),
                (
                    'content_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='contenttypes.ContentType',
                    ),
                ),
            ],
            options={
                'ordering': ('relation_type', 'published'),
            },
        ),
    ]
