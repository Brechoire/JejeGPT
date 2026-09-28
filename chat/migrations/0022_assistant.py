import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0021_export_html_eml"),
    ]

    operations = [
        migrations.CreateModel(
            name="Assistant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160, unique=True)),
                ("description", models.CharField(blank=True, default="", max_length=300)),
                ("instructions", models.TextField(blank=True, default="")),
                ("temperature", models.FloatField(default=0.7)),
                ("reasoning_effort", models.CharField(blank=True, default="", max_length=10)),
                ("web_search", models.BooleanField(default=False)),
                ("thinking_enabled", models.BooleanField(default=True)),
                (
                    "starters",
                    models.TextField(
                        blank=True, default="",
                        help_text="Un starter par ligne (4 max, texte brut).",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="conversation",
            name="assistant",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="conversations", to="chat.assistant",
            ),
        ),
    ]
