from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0024_seed_assistants_vague1"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="reasoning",
            field=models.TextField(blank=True, default=""),
        ),
    ]
