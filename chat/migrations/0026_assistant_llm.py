import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0025_message_reasoning"),
    ]

    operations = [
        migrations.AddField(
            model_name="assistant",
            name="provider",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="assistants", to="chat.provider",
            ),
        ),
        migrations.AddField(
            model_name="assistant",
            name="model_id",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
    ]
