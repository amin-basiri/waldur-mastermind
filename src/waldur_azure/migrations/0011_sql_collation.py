# Generated by Django 1.11.18 on 2019-02-04 16:26
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('waldur_azure', '0010_sql_database'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sqldatabase',
            name='charset',
            field=models.CharField(
                blank=True, default='utf8', max_length=255, null=True
            ),
        ),
        migrations.AlterField(
            model_name='sqldatabase',
            name='collation',
            field=models.CharField(
                blank=True, default='utf8_general_ci', max_length=255, null=True
            ),
        ),
    ]
