from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("sharing", "0001_initial"),
        ("vault", "0002_material_sensitivity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Comment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="material_comments",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("material", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="comments",
                    to="vault.material",
                )),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
    ]
