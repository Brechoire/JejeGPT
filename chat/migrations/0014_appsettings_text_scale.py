from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0013_seed_recherche_template'),
    ]

    operations = [
        migrations.AddField(
            model_name='appsettings',
            name='text_scale',
            field=models.PositiveIntegerField(default=100),
        ),
    ]
