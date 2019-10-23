# -*- coding: utf-8 -*-
# Generated by Django 1.11.1 on 2017-10-16 15:16
from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('waldur_slurm', '0002_add_gpu_ram_quotas'),
    ]

    operations = [
        migrations.CreateModel(
            name='AllocationUsage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(max_length=32)),
                ('year', models.PositiveSmallIntegerField()),
                ('month', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(12)])),
                ('cpu_usage', models.IntegerField(default=0)),
                ('ram_usage', models.IntegerField(default=0)),
                ('gpu_usage', models.IntegerField(default=0)),
                ('allocation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='waldur_slurm.Allocation')),
                ('user', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['allocation'],
            },
        ),
    ]
