# Generated by Django 3.2.13 on 2022-06-09 12:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('openstack_tenant', '0026_instance_hypervisor_hostname'),
    ]

    operations = [
        migrations.AlterField(
            model_name='instance',
            name='security_groups',
            field=models.ManyToManyField(
                blank=True,
                related_name='instances',
                to='openstack_tenant.SecurityGroup',
            ),
        ),
        migrations.AlterField(
            model_name='instance',
            name='server_groups',
            field=models.ManyToManyField(
                blank=True, related_name='instances', to='openstack_tenant.ServerGroup'
            ),
        ),
    ]
