from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("teamcomms_inflight", "0001_initial")]
    operations = [migrations.RunSQL("""
        CREATE TRIGGER tc_workreceipt_immutable BEFORE UPDATE OR DELETE
            ON teamcomms_inflight_workreceipt FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE FUNCTION tc_work_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE t uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Work and its accountable owner are retained' USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.entry_id IS DISTINCT FROM OLD.entry_id THEN
                RAISE EXCEPTION 'Work identity cannot change' USING ERRCODE = '23514';
            END IF;
            SELECT team_id INTO t FROM teamcomms_entries_entry WHERE id=NEW.entry_id AND kind='inflight';
            IF t IS NULL OR NOT EXISTS (SELECT 1 FROM teamcomms_service_membership WHERE team_id=t AND participant_id=NEW.owner_id)
                OR (NEW.executor_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM teamcomms_service_membership WHERE team_id=t AND participant_id=NEW.executor_id))
                OR (NEW.session_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM teamcomms_comms_session s
                    JOIN teamcomms_service_membership m ON s.membership_id=m.id
                    WHERE s.id=NEW.session_id AND m.team_id=t AND m.participant_id=NEW.executor_id)) THEN
                RAISE EXCEPTION 'Work requires same-team owner and executor identity' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_work_binding_guard BEFORE INSERT OR UPDATE OR DELETE
            ON teamcomms_inflight_work FOR EACH ROW EXECUTE FUNCTION tc_work_binding_guard();
        CREATE FUNCTION tc_work_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM teamcomms_inflight_work WHERE entry_id=OLD.id)
                AND (NEW.team_id IS DISTINCT FROM OLD.team_id OR NEW.kind IS DISTINCT FROM OLD.kind OR NEW.id IS DISTINCT FROM OLD.id) THEN
                RAISE EXCEPTION 'Work entry identity, kind and team are permanent' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_work_entry_guard BEFORE UPDATE ON teamcomms_entries_entry
            FOR EACH ROW EXECUTE FUNCTION tc_work_entry_guard();
    """, reverse_sql="""
        DROP TRIGGER tc_work_entry_guard ON teamcomms_entries_entry;
        DROP FUNCTION tc_work_entry_guard();
        DROP TRIGGER tc_work_binding_guard ON teamcomms_inflight_work;
        DROP FUNCTION tc_work_binding_guard();
        DROP TRIGGER tc_workreceipt_immutable ON teamcomms_inflight_workreceipt;
    """)]
