# Generated by Django 2.2.13 on 2020-08-12 17:04

from django.core.exceptions import ObjectDoesNotExist
from django.db import migrations


def update_invoice_items_scope(apps, schema_editor):
    InvoiceItem = apps.get_model('invoices', 'InvoiceItem')
    Resource = apps.get_model('marketplace', 'Resource')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    SupportOffering = apps.get_model('support', 'Offering')

    for invoice_item in InvoiceItem.objects.filter(
        content_type_id=ContentType.objects.get_for_model(SupportOffering).id
    ):
        try:
            resource = Resource.objects.get(
                content_type_id=ContentType.objects.get_for_model(SupportOffering).id,
                object_id=invoice_item.object_id,
            )
            print(
                f'Found Resource {resource.id} for Support.Offering {invoice_item.object_id} for invoice item '
                f'{invoice_item.name} / {invoice_item.invoice.month}-{invoice_item.invoice.year} / {invoice_item.id}'
            )
            invoice_item.content_type_id = ContentType.objects.get_for_model(
                Resource
            ).id
            invoice_item.object_id = resource.id
            invoice_item.save()
        except ObjectDoesNotExist:
            print(
                f'Cannot lookup Resource for Support.Offering {invoice_item.object_id} for invoice item '
                f'{invoice_item.name} / {invoice_item.invoice.month}-{invoice_item.invoice.year} / {invoice_item.id}'
            )


def update_invoice_items_resource_name(apps, schema_editor):
    InvoiceItem = apps.get_model('invoices', 'InvoiceItem')
    Resource = apps.get_model('marketplace', 'Resource')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    resource_ctid = ContentType.objects.get_for_model(Resource).id

    for invoice_item in InvoiceItem.objects.filter(content_type_id=resource_ctid):
        try:
            resource = Resource.objects.get(id=invoice_item.object_id,)
            invoice_item.details.update(
                {'resource_name': resource.name, 'resource_uuid': resource.uuid.hex,}
            )
            invoice_item.save()
        except ObjectDoesNotExist:
            print(
                f'Cannot lookup Resource {invoice_item.object_id} for invoice item '
                f'{invoice_item.name} / {invoice_item.invoice.month}-{invoice_item.invoice.year} / {invoice_item.id}'
            )


class Migration(migrations.Migration):
    dependencies = [
        ('invoices', '0041_update_invoice_items_scope'),
    ]

    operations = [
        migrations.RunPython(update_invoice_items_scope, migrations.RunPython.noop),
        migrations.RunPython(
            update_invoice_items_resource_name, migrations.RunPython.noop
        ),
    ]
