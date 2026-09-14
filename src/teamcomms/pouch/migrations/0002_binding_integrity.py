from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("teamcomms_pouch", "0001_initial"), ("teamcomms_entries", "0004_editplan_integrity")]
    operations = [migrations.RunSQL("""
        CREATE FUNCTION tc_pouch_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Canonical Pouch binding is permanent' USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM teamcomms_entries_entry
                           WHERE id = NEW.entry_id AND team_id = NEW.team_id AND kind = 'document') THEN
                RAISE EXCEPTION 'Pouch must bind a document in its team' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_pouch_binding_guard BEFORE INSERT OR UPDATE OR DELETE ON teamcomms_pouch_pouch
            FOR EACH ROW EXECUTE FUNCTION tc_pouch_binding_guard();
        CREATE FUNCTION tc_pouch_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.team_id IS DISTINCT FROM OLD.team_id OR NEW.kind IS DISTINCT FROM OLD.kind)
               AND EXISTS (SELECT 1 FROM teamcomms_pouch_pouch WHERE entry_id = OLD.id) THEN
                RAISE EXCEPTION 'Pouch team and document kind are permanent' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_pouch_entry_guard BEFORE UPDATE OF team_id, kind ON teamcomms_entries_entry
            FOR EACH ROW EXECUTE FUNCTION tc_pouch_entry_guard();
        """, reverse_sql="""
        DROP TRIGGER tc_pouch_entry_guard ON teamcomms_entries_entry;
        DROP FUNCTION tc_pouch_entry_guard();
        DROP TRIGGER tc_pouch_binding_guard ON teamcomms_pouch_pouch;
        DROP FUNCTION tc_pouch_binding_guard();
        """)]
