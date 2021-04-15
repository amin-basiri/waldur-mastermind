# Generated by Django 2.2.20 on 2021-04-15 11:19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('openstack', '0023_drop_spl'),
    ]

    operations = [
        migrations.AlterField(
            model_name='securitygrouprule',
            name='backend_id',
            field=models.CharField(blank=True, max_length=36),
        ),
        migrations.AlterField(
            model_name='securitygrouprule',
            name='ethertype',
            field=models.CharField(
                choices=[('IPv4', 'IPv4'), ('IPv6', 'IPv6')],
                default='IPv4',
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name='securitygrouprule',
            name='protocol',
            field=models.CharField(
                blank=True,
                choices=[('tcp', 'tcp'), ('udp', 'udp'), ('icmp', 'icmp')],
                max_length=40,
            ),
        ),
    ]
