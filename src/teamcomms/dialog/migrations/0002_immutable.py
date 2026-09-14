from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("teamcomms_dialog", "0001_initial")]
    operations = [migrations.RunSQL("""
        CREATE FUNCTION tc_dialog_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Dialog evidence is immutable' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER tc_dialog_event_immutable BEFORE UPDATE OR DELETE ON teamcomms_dialog_event
        FOR EACH ROW EXECUTE FUNCTION tc_dialog_immutable();
    """, """
        DROP TRIGGER tc_dialog_event_immutable ON teamcomms_dialog_event;
        DROP FUNCTION tc_dialog_immutable();
    """)]
