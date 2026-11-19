"""Record each session's last use; existing sessions start at the migration time."""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('teamcomms_comms', '0003_message_history'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='last_active_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
