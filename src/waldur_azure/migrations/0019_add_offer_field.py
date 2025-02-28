# Generated by Django 2.2.20 on 2021-05-21 15:44

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('waldur_azure', '0018_drop_spl'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='image',
            options={'ordering': ['publisher', 'offer', 'name', 'sku']},
        ),
        migrations.AddField(
            model_name='image',
            name='offer',
            field=models.CharField(default='offer', max_length=255),
            preserve_default=False,
        ),
    ]
