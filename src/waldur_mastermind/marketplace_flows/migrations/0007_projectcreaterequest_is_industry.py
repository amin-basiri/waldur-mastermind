# Generated by Django 3.2.12 on 2022-03-04 11:14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace_flows', '0006_json_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectcreaterequest',
            name='is_industry',
            field=models.BooleanField(default=False),
        ),
    ]
