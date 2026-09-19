import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0015_project_memory'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeepResearchRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject', models.CharField(max_length=300)),
                ('status', models.CharField(choices=[('running', 'En cours'), ('done', 'Terminée'), ('error', 'En erreur')], default='running', max_length=8)),
                ('step', models.CharField(blank=True, default='', max_length=200)),
                ('cycles_done', models.PositiveIntegerField(default=0)),
                ('max_cycles', models.PositiveIntegerField(default=3)),
                ('error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('conversation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='research_runs', to='chat.conversation')),
                ('source', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='chat.conversation')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
