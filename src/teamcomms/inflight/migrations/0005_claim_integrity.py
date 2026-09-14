from django.db import migrations


class Migration(migrations.Migration):
    dependencies=[('teamcomms_inflight','0004_guardrun')]
    operations=[migrations.RunSQL('''
        CREATE TRIGGER tc_coordination_receipt_immutable BEFORE UPDATE OR DELETE ON teamcomms_inflight_coordinationreceipt
            FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE FUNCTION tc_claim_record_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Coordination records are retained' USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'teamcomms_inflight_workoffer' THEN
                IF (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state')
                    OR OLD.state <> 'open' OR NEW.state NOT IN ('claimed','canceled') THEN
                    RAISE EXCEPTION 'Offer identity and specification are immutable' USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'teamcomms_inflight_claim' THEN
                IF (to_jsonb(NEW) - ARRAY['state','deadline']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','deadline'])
                    OR OLD.state <> 'active' OR NEW.state NOT IN ('active','released','stopped','completed') THEN
                    RAISE EXCEPTION 'Claim identity or closed outcome cannot change' USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'teamcomms_inflight_guardrun' THEN
                IF (to_jsonb(NEW) - ARRAY['state','outcome']) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','outcome'])
                    OR OLD.state <> 'active' OR NEW.state NOT IN ('finished','reconciled') THEN
                    RAISE EXCEPTION 'Guard identity or terminal outcome cannot change' USING ERRCODE = '23514';
                END IF;
            ELSIF TG_TABLE_NAME = 'teamcomms_inflight_reservation' THEN
                IF (to_jsonb(NEW) - 'active') IS DISTINCT FROM (to_jsonb(OLD) - 'active') OR NOT OLD.active OR NEW.active THEN
                    RAISE EXCEPTION 'Reservation identity cannot change or reactivate' USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_offer_guard BEFORE UPDATE OR DELETE ON teamcomms_inflight_workoffer FOR EACH ROW EXECUTE FUNCTION tc_claim_record_guard();
        CREATE TRIGGER tc_claim_guard BEFORE UPDATE OR DELETE ON teamcomms_inflight_claim FOR EACH ROW EXECUTE FUNCTION tc_claim_record_guard();
        CREATE TRIGGER tc_guardrun_guard BEFORE UPDATE OR DELETE ON teamcomms_inflight_guardrun FOR EACH ROW EXECUTE FUNCTION tc_claim_record_guard();
        CREATE TRIGGER tc_reservation_guard BEFORE UPDATE OR DELETE ON teamcomms_inflight_reservation FOR EACH ROW EXECUTE FUNCTION tc_claim_record_guard();
        CREATE FUNCTION tc_custody_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Resource custody is retained' USING ERRCODE = '23514';
            END IF;
            IF NEW.generation < 1 OR NEW.protection NOT IN ('advisory','local_flock') OR NOT EXISTS (
                SELECT 1 FROM teamcomms_service_membership m JOIN teamcomms_comms_resource r ON r.team_id=m.team_id
                WHERE r.id=NEW.resource_id AND m.participant_id=NEW.custodian_id) THEN
                RAISE EXCEPTION 'Resource needs a same-team custodian and valid protection' USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE' AND (NEW.resource_id IS DISTINCT FROM OLD.resource_id OR NEW.protection IS DISTINCT FROM OLD.protection OR NEW.generation < OLD.generation) THEN
                RAISE EXCEPTION 'Resource identity/protection is permanent and generations cannot regress' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_custody_guard BEFORE INSERT OR UPDATE OR DELETE ON teamcomms_inflight_resourcecustody FOR EACH ROW EXECUTE FUNCTION tc_custody_guard();
    ''',reverse_sql='''
        DROP TRIGGER tc_custody_guard ON teamcomms_inflight_resourcecustody;
        DROP FUNCTION tc_custody_guard();
        DROP TRIGGER tc_reservation_guard ON teamcomms_inflight_reservation;
        DROP TRIGGER tc_guardrun_guard ON teamcomms_inflight_guardrun;
        DROP TRIGGER tc_claim_guard ON teamcomms_inflight_claim;
        DROP TRIGGER tc_offer_guard ON teamcomms_inflight_workoffer;
        DROP FUNCTION tc_claim_record_guard();
        DROP TRIGGER tc_coordination_receipt_immutable ON teamcomms_inflight_coordinationreceipt;
    ''')]
