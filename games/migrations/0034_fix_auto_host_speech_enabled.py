from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0033_gamesettings_typewriter_enabled_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE games_gamesettings ADD COLUMN IF NOT EXISTS auto_host_speech_enabled BOOLEAN NOT NULL DEFAULT FALSE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]