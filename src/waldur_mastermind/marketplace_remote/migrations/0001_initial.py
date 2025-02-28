# Generated by Django 2.2.24 on 2021-12-03 13:44

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
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectUpdateRequest',
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
                ('uuid', waldur_core.core.fields.UUIDField()),
                (
                    'name',
                    models.CharField(
                        max_length=500,
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
                    'oecd_fos_2007_code',
                    models.CharField(
                        blank=True,
                        choices=[
                            ('1.1', 'Mathematics'),
                            ('1.2', 'Computer and information sciences'),
                            ('1.3', 'Physical sciences'),
                            ('1.4', 'Chemical sciences'),
                            ('1.5', 'Earth and related environmental sciences'),
                            ('1.6', 'Biological sciences'),
                            ('1.7', 'Other natural sciences'),
                            ('2.1', 'Civil engineering'),
                            (
                                '2.2',
                                'Electrical engineering, electronic engineering, information engineering',
                            ),
                            ('2.3', 'Mechanical engineering'),
                            ('2.4', 'Chemical engineering'),
                            ('2.5', 'Materials engineering'),
                            ('2.6', 'Medical engineering'),
                            ('2.7', 'Environmental engineering'),
                            ('2.8', 'Systems engineering'),
                            ('2.9', 'Environmental biotechnology'),
                            ('2.10', 'Industrial biotechnology'),
                            ('2.11', 'Nano technology'),
                            ('2.12', 'Other engineering and technologies'),
                            ('3.1', 'Basic medicine'),
                            ('3.2', 'Clinical medicine'),
                            ('3.3', 'Health sciences'),
                            ('3.4', 'Health biotechnology'),
                            ('3.5', 'Other medical sciences'),
                            ('4.1', 'Agriculture, forestry, and fisheries'),
                            ('4.2', 'Animal and dairy science'),
                            ('4.3', 'Veterinary science'),
                            ('4.4', 'Agricultural biotechnology'),
                            ('4.5', 'Other agricultural sciences'),
                            ('5.1', 'Psychology'),
                            ('5.2', 'Economics and business'),
                            ('5.3', 'Educational sciences'),
                            ('5.4', 'Sociology'),
                            ('5.5', 'Law'),
                            ('5.6', 'Political science'),
                            ('5.7', 'Social and economic geography'),
                            ('5.8', 'Media and communications'),
                            ('5.9', 'Other social sciences'),
                            ('6.1', 'History and archaeology'),
                            ('6.2', 'Languages and literature'),
                            ('6.3', 'Philosophy, ethics and religion'),
                            (
                                '6.4',
                                'Arts (arts, history of arts, performing arts, music)',
                            ),
                            ('6.5', 'Other humanities'),
                        ],
                        max_length=80,
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
                    'project',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='structure.Project',
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
                (
                    'type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to='structure.ProjectType',
                        verbose_name='project type',
                    ),
                ),
            ],
            options={
                'ordering': ['created'],
            },
        ),
    ]
