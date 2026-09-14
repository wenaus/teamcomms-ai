from django.db import migrations


class Migration(migrations.Migration):
    dependencies=[('teamcomms_capcom','0001_initial')]
    operations=[migrations.RunSQL('''
        CREATE TRIGGER tc_capcom_notice_immutable BEFORE UPDATE OR DELETE ON teamcomms_capcom_notice FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE TRIGGER tc_capcom_revision_immutable BEFORE UPDATE OR DELETE ON teamcomms_capcom_topicrevision FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE TRIGGER tc_capcom_reference_immutable BEFORE UPDATE OR DELETE ON teamcomms_capcom_topicreference FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE TRIGGER tc_capcom_receipt_immutable BEFORE UPDATE OR DELETE ON teamcomms_capcom_mutationreceipt FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE FUNCTION tc_capcom_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Capcom coordination history is retained' USING ERRCODE='23514'; END IF;
            IF TG_TABLE_NAME='teamcomms_capcom_topic' THEN
                IF NEW.team_id IS DISTINCT FROM OLD.team_id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.key IS DISTINCT FROM OLD.key
                    OR NEW.revision < OLD.revision OR NEW.last_sequence < OLD.last_sequence THEN
                    RAISE EXCEPTION 'Topic identity and monotonic sequence are retained' USING ERRCODE='23514';
                END IF;
            ELSIF TG_TABLE_NAME='teamcomms_capcom_decision' THEN
                IF OLD.resolved_at IS NOT NULL OR NEW.notice_id IS DISTINCT FROM OLD.notice_id OR NEW.revision <> OLD.revision+1
                    OR NEW.resolved_at IS NULL OR NEW.resolved_by_id IS NULL OR NEW.resolution='' THEN
                    RAISE EXCEPTION 'Decision needs explicit final resolution' USING ERRCODE='23514';
                END IF;
            ELSIF TG_TABLE_NAME='teamcomms_capcom_presentation' THEN
                IF OLD.disposition IN ('recorded','coalesced') OR NEW.delivery_id IS DISTINCT FROM OLD.delivery_id THEN
                    RAISE EXCEPTION 'Final presentation disposition is retained' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER tc_capcom_topic_guard BEFORE UPDATE OR DELETE ON teamcomms_capcom_topic FOR EACH ROW EXECUTE FUNCTION tc_capcom_guard();
        CREATE TRIGGER tc_capcom_decision_guard BEFORE UPDATE OR DELETE ON teamcomms_capcom_decision FOR EACH ROW EXECUTE FUNCTION tc_capcom_guard();
        CREATE TRIGGER tc_capcom_presentation_guard BEFORE UPDATE OR DELETE ON teamcomms_capcom_presentation FOR EACH ROW EXECUTE FUNCTION tc_capcom_guard();
    ''',reverse_sql='''
        DROP TRIGGER tc_capcom_presentation_guard ON teamcomms_capcom_presentation;
        DROP TRIGGER tc_capcom_decision_guard ON teamcomms_capcom_decision;
        DROP TRIGGER tc_capcom_topic_guard ON teamcomms_capcom_topic;
        DROP FUNCTION tc_capcom_guard();
        DROP TRIGGER tc_capcom_receipt_immutable ON teamcomms_capcom_mutationreceipt;
        DROP TRIGGER tc_capcom_reference_immutable ON teamcomms_capcom_topicreference;
        DROP TRIGGER tc_capcom_revision_immutable ON teamcomms_capcom_topicrevision;
        DROP TRIGGER tc_capcom_notice_immutable ON teamcomms_capcom_notice;
    ''')]
