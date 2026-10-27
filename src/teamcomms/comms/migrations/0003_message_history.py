"""Index existing publications without rewriting immutable message evidence."""

from django.db import migrations, models
import django.db.models.deletion


SQL = """
-- Freeze publication while backfilling and installing the insertion guard.
LOCK TABLE teamcomms_comms_message IN SHARE ROW EXCLUSIVE MODE;
INSERT INTO teamcomms_comms_messageposition (message_id)
SELECT id FROM teamcomms_comms_message ORDER BY created_at, id;

CREATE FUNCTION tc_message_position() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Allocate positions only after this team's preceding publisher commits.
  PERFORM pg_advisory_xact_lock(hashtextextended('tc-message-history:' || NEW.team_id::text, 0));
  INSERT INTO teamcomms_comms_messageposition (message_id) VALUES (NEW.id);
  RETURN NEW;
END $$;
CREATE TRIGGER tc_message_position AFTER INSERT ON teamcomms_comms_message
FOR EACH ROW EXECUTE FUNCTION tc_message_position();
CREATE TRIGGER tc_message_position_immutable BEFORE UPDATE OR DELETE ON teamcomms_comms_messageposition
FOR EACH ROW EXECUTE FUNCTION tc_comms_immutable();
"""

REVERSE = """
DROP TRIGGER tc_message_position ON teamcomms_comms_message;
DROP FUNCTION tc_message_position();
DROP TRIGGER tc_message_position_immutable ON teamcomms_comms_messageposition;
"""


class Migration(migrations.Migration):
    dependencies = [("teamcomms_comms", "0002_immutable_records")]
    operations = [
        migrations.CreateModel(
            name="MessagePosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("message", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to="teamcomms_comms.message")),
            ],
        ),
        migrations.RunSQL(SQL, REVERSE),
    ]
