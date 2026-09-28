# Generated for JejeGPT image gallery (show_images toggle).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0019_seed_quiz_infographie'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='show_images',
            field=models.BooleanField(default=True),
        ),
    ]
