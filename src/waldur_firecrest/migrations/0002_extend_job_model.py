# Generated by Django 2.2.24 on 2021-08-12 12:31

import django.contrib.postgres.fields.jsonb
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('waldur_firecrest', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='file',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='slurm_jobs',
                verbose_name='Batch script file',
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='report',
            field=django.contrib.postgres.fields.jsonb.JSONField(
                blank=True, null=True, verbose_name='Job output'
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='runtime_state',
            field=models.CharField(
                default='',
                max_length=100,
                blank=True,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='job',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Reference to user which submitted job',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
