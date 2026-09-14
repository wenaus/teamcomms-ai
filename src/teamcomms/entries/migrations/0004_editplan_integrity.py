from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("teamcomms_entries", "0003_editplan")]
    operations = [migrations.RunSQL("""
        CREATE FUNCTION tc_editplan_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Edit plans are retained' USING ERRCODE = '23514';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.team_id IS DISTINCT FROM OLD.team_id
               OR NEW.author_id IS DISTINCT FROM OLD.author_id OR NEW.request IS DISTINCT FROM OLD.request
               OR NEW.prepared IS DISTINCT FROM OLD.prepared OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR OLD.result IS NOT NULL THEN
                RAISE EXCEPTION 'Edit plan inputs and terminal outcomes are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_editplan_immutable BEFORE UPDATE OR DELETE ON teamcomms_entries_editplan
            FOR EACH ROW EXECUTE FUNCTION tc_editplan_immutable();
        """, reverse_sql="""
        DROP TRIGGER tc_editplan_immutable ON teamcomms_entries_editplan;
        DROP FUNCTION tc_editplan_immutable();
        """)]
