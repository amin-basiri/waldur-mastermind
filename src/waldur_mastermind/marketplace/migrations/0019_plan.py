# -*- coding: utf-8 -*-
# Generated by Django 1.11.14 on 2018-08-07 07:31
from __future__ import unicode_literals

from decimal import Decimal
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import model_utils.fields
import waldur_core.core.fields
import waldur_core.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0018_serviceprovider_description'),
    ]

    operations = [
        migrations.CreateModel(
            name='Plan',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('description', models.CharField(blank=True, max_length=500, verbose_name='description')),
                ('name', models.CharField(max_length=150, validators=[waldur_core.core.validators.validate_name], verbose_name='name')),
                ('uuid', waldur_core.core.fields.UUIDField()),
                ('unit_price', models.DecimalField(decimal_places=7, default=0, max_digits=22, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('unit', models.CharField(choices=[('month', 'Per month'), ('half_month', 'Per half month'), ('day', 'Per day'), ('hour', 'Per hour'), ('quantity', 'Quantity')], default='day', max_length=30)),
                ('offering', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='plans', to='marketplace.Offering')),
            ],
            options={
                'abstract': False,
                'ordering': ('name',)
            },
        ),
        migrations.AddField(
            model_name='orderitem',
            name='plan',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='marketplace.Plan'),
        ),
    ]
