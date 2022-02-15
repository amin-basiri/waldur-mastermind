# Generated by Django 2.2.10 on 2020-03-30 08:06

import django.db.models.deletion
from django.db import migrations, models

import waldur_core.core.fields
import waldur_core.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace_checklist', '0002_question_solution'),
    ]

    operations = [
        migrations.CreateModel(
            name='Category',
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
                    'description',
                    models.CharField(
                        blank=True, max_length=500, verbose_name='description'
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
                ('uuid', waldur_core.core.fields.UUIDField()),
            ],
            options={'ordering': ('name',), 'verbose_name_plural': 'Categories'},
        ),
        migrations.AlterModelOptions(
            name='checklist',
            options={'ordering': ('name',)},
        ),
        migrations.AddField(
            model_name='question',
            name='correct_answer',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='question',
            name='solution',
            field=models.TextField(
                blank=True,
                help_text='It is shown when incorrect or N/A answer is chosen',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='checklist',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='checklists',
                to='marketplace_checklist.Category',
            ),
        ),
    ]
