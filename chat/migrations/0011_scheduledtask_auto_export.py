from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0010_scheduled_tasks'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduledtask',
            name='auto_export',
            field=models.CharField(blank=True, default='', max_length=4),
        ),
    ]
