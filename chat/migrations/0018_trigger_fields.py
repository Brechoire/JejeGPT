from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0017_p0c_scheduled'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduledtask',
            name='trigger_url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='trigger_mode',
            field=models.CharField(choices=[('change', 'Changement'), ('keyword', 'Mot-clé')], default='change', max_length=8),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='trigger_keyword',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='last_check',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='last_trigger_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='sites',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='scheduledrun',
            name='cause',
            field=models.CharField(choices=[('due', 'Échéance'), ('catchup', 'Rattrapage'), ('manual', 'Manuelle'), ('draft', 'Brouillon'), ('trigger', 'Déclenchement')], default='due', max_length=8),
        ),
    ]
