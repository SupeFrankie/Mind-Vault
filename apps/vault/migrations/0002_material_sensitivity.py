from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="material",
            name="sensitivity",
            field=models.CharField(
                blank=True,
                choices=[
                    ("public", "Public"),
                    ("internal", "Internal"),
                    ("confidential", "Confidential"),
                    ("highly_confidential", "Highly Confidential"),
                ],
                max_length=20,
                null=True,
            ),
        ),
    ]
