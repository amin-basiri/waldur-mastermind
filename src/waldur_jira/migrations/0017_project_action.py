# -*- coding: utf-8 -*-
# Generated by Django 1.11.7 on 2018-05-17 09:51
from django.db import migrations, models
import waldur_core.core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('waldur_jira', '0016_project_template_null'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='action',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='project',
            name='action_details',
            field=waldur_core.core.fields.JSONField(default={}),
        ),
    ]
