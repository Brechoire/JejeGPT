from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0016_deepresearchrun'),
    ]

    operations = [
        migrations.AlterField(
            model_name='scheduledtask',
            name='frequency',
            field=models.CharField(choices=[('daily', 'Quotidien'), ('weekly', 'Hebdomadaire'), ('monthly', 'Mensuel'), ('once', 'Une fois')], default='daily', max_length=8),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='month_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='last_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='last_excerpt',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='scheduledrun',
            name='cause',
            field=models.CharField(choices=[('due', 'Échéance'), ('catchup', 'Rattrapage'), ('manual', 'Manuelle'), ('draft', 'Brouillon')], default='due', max_length=8),
        ),
        migrations.AddField(
            model_name='scheduledrun',
            name='has_changes',
            field=models.BooleanField(default=True),
        ),
    ]
