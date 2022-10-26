# Generated by Django 3.2.14 on 2022-10-25 20:27

from django.db import migrations, models

import waldur_core.core.fields
import waldur_core.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_rename_notification_broadcastmessage'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageTemplate',
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
                (
                    'name',
                    models.CharField(
                        max_length=150,
                        validators=[waldur_core.core.validators.validate_name],
                        verbose_name='name',
                    ),
                ),
                ('body', models.TextField()),
                ('subject', models.TextField()),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
