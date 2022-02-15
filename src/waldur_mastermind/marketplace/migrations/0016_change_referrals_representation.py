# Generated by Django 2.2.10 on 2020-04-24 10:18

from django.db import migrations

import waldur_core.core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0015_add_citation_info'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='offering',
            name='referred_pids',
        ),
        migrations.AddField(
            model_name='offering',
            name='referrals',
            field=waldur_core.core.fields.JSONField(
                blank=True, default=dict, help_text='Referrals list for the current DOI'
            ),
        ),
    ]
