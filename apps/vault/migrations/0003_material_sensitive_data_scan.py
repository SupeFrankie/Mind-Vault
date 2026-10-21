from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0002_material_sensitivity"),
    ]

    operations = [
        migrations.AddField(
            model_name="material",
            name="sensitive_data_scan",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
    ]
