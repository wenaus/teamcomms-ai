from django.db import migrations


class Migration(migrations.Migration):
    dependencies=[('teamcomms_inflight','0006_executionrun')]
    operations=[migrations.RunSQL('''
        CREATE FUNCTION tc_execution_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Execution history is retained' USING ERRCODE='23514'; END IF;
            IF (to_jsonb(NEW)-ARRAY['state','result']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','result'])
                OR OLD.state<>'active' OR NEW.state NOT IN ('finished','reconciled') OR NEW.result IS NULL THEN
                RAISE EXCEPTION 'Execution identity and final result are immutable' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER tc_execution_guard BEFORE UPDATE OR DELETE ON teamcomms_inflight_executionrun
            FOR EACH ROW EXECUTE FUNCTION tc_execution_guard();
    ''',reverse_sql='''DROP TRIGGER tc_execution_guard ON teamcomms_inflight_executionrun; DROP FUNCTION tc_execution_guard();''')]
