from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("teamcomms_entries", "0001_initial")]

    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION tc_entry_search_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', replace(
                coalesce(NEW.state->>'title', '') || ' ' ||
                coalesce(NEW.state->>'content', '') || ' ' || coalesce(NEW.slug, ''), '/', ' '));
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER tc_entry_search_update BEFORE INSERT OR UPDATE OF state, slug
            ON teamcomms_entries_entry FOR EACH ROW EXECUTE FUNCTION tc_entry_search_update();
        UPDATE teamcomms_entries_entry SET state = state;

        CREATE FUNCTION tc_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Entry revisions and their references are immutable'
                USING ERRCODE = '23514';
        END;
        $$;
        CREATE TRIGGER tc_revision_immutable BEFORE UPDATE OR DELETE
            ON teamcomms_entries_revision FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        CREATE TRIGGER tc_reference_immutable BEFORE UPDATE OR DELETE
            ON teamcomms_entries_revisionreference FOR EACH ROW EXECUTE FUNCTION tc_revision_immutable();
        ALTER TABLE teamcomms_entries_revisionreference ADD CONSTRAINT tc_reference_target
            FOREIGN KEY (target_entry_id, target_revision_id)
            REFERENCES teamcomms_entries_revision (entry_id, id);
        """,
        reverse_sql="""
        ALTER TABLE teamcomms_entries_revisionreference DROP CONSTRAINT tc_reference_target;
        DROP TRIGGER tc_reference_immutable ON teamcomms_entries_revisionreference;
        DROP TRIGGER tc_revision_immutable ON teamcomms_entries_revision;
        DROP FUNCTION tc_revision_immutable();
        DROP TRIGGER tc_entry_search_update ON teamcomms_entries_entry;
        DROP FUNCTION tc_entry_search_update();
        """,
    )]
