"""Preserve publication/receipt evidence and coherent fixed entry references."""
from django.db import migrations

SQL = """
CREATE FUNCTION tc_comms_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Communication evidence is immutable' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER tc_message_immutable BEFORE UPDATE OR DELETE ON teamcomms_comms_message
FOR EACH ROW EXECUTE FUNCTION tc_comms_immutable();
CREATE TRIGGER tc_receipt_immutable BEFORE UPDATE OR DELETE ON teamcomms_comms_receipt
FOR EACH ROW EXECUTE FUNCTION tc_comms_immutable();
CREATE TRIGGER tc_message_ref_immutable BEFORE UPDATE OR DELETE ON teamcomms_comms_messagereference
FOR EACH ROW EXECUTE FUNCTION tc_comms_immutable();
ALTER TABLE teamcomms_comms_messagereference ADD CONSTRAINT tc_message_ref_revision
FOREIGN KEY (entry_id, revision_id) REFERENCES teamcomms_entries_revision (entry_id, id);
"""
REVERSE = """
ALTER TABLE teamcomms_comms_messagereference DROP CONSTRAINT tc_message_ref_revision;
DROP TRIGGER tc_message_ref_immutable ON teamcomms_comms_messagereference;
DROP TRIGGER tc_receipt_immutable ON teamcomms_comms_receipt;
DROP TRIGGER tc_message_immutable ON teamcomms_comms_message;
DROP FUNCTION tc_comms_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("teamcomms_comms", "0001_initial"), ("teamcomms_entries", "0002_integrity_and_search")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
