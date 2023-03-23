# Generated by Django 2.2.24 on 2021-06-08 19:43

import django.contrib.postgres.fields.jsonb
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import django_fsm
import model_utils.fields
from django.conf import settings
from django.db import migrations, models

import waldur_core.core.fields
import waldur_core.core.validators


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('structure', '0001_squashed_0036'),
        ('marketplace', '0001_squashed_0076'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerCreateRequest',
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
                    'name',
                    models.CharField(
                        max_length=150,
                        validators=[waldur_core.core.validators.validate_name],
                        verbose_name='name',
                    ),
                ),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('longitude', models.FloatField(blank=True, null=True)),
                (
                    'vat_code',
                    models.CharField(blank=True, help_text='VAT number', max_length=20),
                ),
                (
                    'vat_name',
                    models.CharField(
                        blank=True,
                        help_text='Optional business name retrieved for the VAT number.',
                        max_length=255,
                    ),
                ),
                (
                    'vat_address',
                    models.CharField(
                        blank=True,
                        help_text='Optional business address retrieved for the VAT number.',
                        max_length=255,
                    ),
                ),
                ('country', models.CharField(blank=True, max_length=2)),
                (
                    'native_name',
                    models.CharField(blank=True, default='', max_length=160),
                ),
                ('abbreviation', models.CharField(blank=True, max_length=12)),
                (
                    'contact_details',
                    models.TextField(
                        blank=True,
                        validators=[django.core.validators.MaxLengthValidator(500)],
                    ),
                ),
                (
                    'agreement_number',
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                (
                    'sponsor_number',
                    models.PositiveIntegerField(
                        blank=True,
                        help_text='External ID of the sponsor covering the costs',
                        null=True,
                    ),
                ),
                (
                    'email',
                    models.EmailField(
                        blank=True, max_length=75, verbose_name='email address'
                    ),
                ),
                (
                    'phone_number',
                    models.CharField(
                        blank=True, max_length=255, verbose_name='phone number'
                    ),
                ),
                (
                    'access_subnets',
                    models.TextField(
                        blank=True,
                        default='',
                        help_text='Enter a comma separated list of IPv4 or IPv6 CIDR addresses from where connection to self-service is allowed.',
                        validators=[waldur_core.core.validators.validate_cidr_list],
                    ),
                ),
                (
                    'backend_id',
                    models.CharField(
                        blank=True,
                        help_text='Organization identifier in another application.',
                        max_length=255,
                    ),
                ),
                (
                    'registration_code',
                    models.CharField(blank=True, default='', max_length=160),
                ),
                ('homepage', models.URLField(blank=True, max_length=255)),
                ('domain', models.CharField(blank=True, max_length=255)),
                ('address', models.CharField(blank=True, max_length=300)),
                ('postal', models.CharField(blank=True, max_length=20)),
                ('bank_name', models.CharField(blank=True, max_length=150)),
                ('bank_account', models.CharField(blank=True, max_length=50)),
                (
                    'state',
                    django_fsm.FSMIntegerField(
                        choices=[
                            (1, 'draft'),
                            (2, 'pending'),
                            (3, 'approved'),
                            (4, 'rejected'),
                            (5, 'canceled'),
                        ],
                        default=1,
                    ),
                ),
                (
                    'reviewed_at',
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ('review_comment', models.TextField(blank=True, null=True)),
                (
                    'reviewed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ResourceCreateRequest',
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
                        blank=True, max_length=2000, verbose_name='description'
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
                (
                    'cost',
                    models.DecimalField(
                        blank=True, decimal_places=10, max_digits=22, null=True
                    ),
                ),
                (
                    'limits',
                    django.contrib.postgres.fields.jsonb.JSONField(
                        blank=True, default=dict
                    ),
                ),
                (
                    'attributes',
                    django.contrib.postgres.fields.jsonb.JSONField(
                        blank=True, default=dict
                    ),
                ),
                (
                    'end_date',
                    models.DateField(
                        blank=True,
                        help_text='The date is inclusive. Once reached, a resource will be scheduled for termination.',
                        null=True,
                    ),
                ),
                (
                    'state',
                    django_fsm.FSMIntegerField(
                        choices=[
                            (1, 'draft'),
                            (2, 'pending'),
                            (3, 'approved'),
                            (4, 'rejected'),
                            (5, 'canceled'),
                        ],
                        default=1,
                    ),
                ),
                (
                    'reviewed_at',
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ('review_comment', models.TextField(blank=True, null=True)),
                (
                    'offering',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to='marketplace.Offering',
                    ),
                ),
                (
                    'plan',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to='marketplace.Plan',
                    ),
                ),
                (
                    'reviewed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ProjectCreateRequest',
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
                        blank=True, max_length=2000, verbose_name='description'
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
                (
                    'end_date',
                    models.DateField(
                        blank=True,
                        help_text='The date is inclusive. Once reached, all project resource will be scheduled for termination.',
                        null=True,
                    ),
                ),
                (
                    'state',
                    django_fsm.FSMIntegerField(
                        choices=[
                            (1, 'draft'),
                            (2, 'pending'),
                            (3, 'approved'),
                            (4, 'rejected'),
                            (5, 'canceled'),
                        ],
                        default=1,
                    ),
                ),
                (
                    'reviewed_at',
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ('review_comment', models.TextField(blank=True, null=True)),
                (
                    'reviewed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='FlowTracker',
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
                ('uuid', waldur_core.core.fields.UUIDField()),
                (
                    'state',
                    django_fsm.FSMIntegerField(
                        choices=[
                            (1, 'draft'),
                            (2, 'pending'),
                            (3, 'approved'),
                            (4, 'rejected'),
                            (5, 'canceled'),
                        ],
                        default=1,
                    ),
                ),
                (
                    'customer',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='structure.Customer',
                    ),
                ),
                (
                    'customer_create_request',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='flow',
                        to='marketplace_flows.CustomerCreateRequest',
                    ),
                ),
                (
                    'order_item',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='marketplace.OrderItem',
                    ),
                ),
                (
                    'project_create_request',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='flow',
                        to='marketplace_flows.ProjectCreateRequest',
                    ),
                ),
                (
                    'requested_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'resource_create_request',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='flow',
                        to='marketplace_flows.ResourceCreateRequest',
                    ),
                ),
            ],
            options={'abstract': False, 'ordering': ['-created']},
        ),
    ]
