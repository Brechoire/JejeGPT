from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0011_scheduledtask_auto_export'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=160, unique=True)),
                ('description', models.CharField(blank=True, default='', max_length=300)),
                ('body', models.TextField()),
                ('suggest_web', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
