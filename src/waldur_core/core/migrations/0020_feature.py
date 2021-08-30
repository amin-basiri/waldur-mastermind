# Generated by Django 2.2.24 on 2021-08-30 08:28

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_drop_zabbix_tables'),
    ]

    operations = [
        migrations.CreateModel(
            name='Feature',
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
                ('key', models.TextField(max_length=255, unique=True)),
                ('value', models.BooleanField(default=False)),
            ],
        ),
    ]
