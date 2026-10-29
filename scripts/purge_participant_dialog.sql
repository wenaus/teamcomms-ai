-- Delete every Dialog event recorded from the sessions of the selected participants.
-- Dialog events are immutable by trigger; this lifts the trigger only inside the
-- transaction, as the table owner, and restores it before commit.
--
-- runuser -u postgres -- psql -d DATABASE -v ON_ERROR_STOP=1 \
--   -v participants="'UUID','UUID'" -f scripts/purge_participant_dialog.sql

BEGIN;
ALTER TABLE teamcomms_dialog_event DISABLE TRIGGER tc_dialog_event_immutable;

SELECT p.name, count(*) AS events
FROM teamcomms_dialog_event e
JOIN teamcomms_comms_session s ON s.id = e.session_id
JOIN teamcomms_service_membership m ON m.id = s.membership_id
JOIN teamcomms_service_participant p ON p.id = m.participant_id
WHERE p.id IN (:participants)
GROUP BY p.name;

DELETE FROM teamcomms_dialog_event e
USING teamcomms_comms_session s, teamcomms_service_membership m
WHERE s.id = e.session_id AND m.id = s.membership_id
  AND m.participant_id IN (:participants);

ALTER TABLE teamcomms_dialog_event ENABLE TRIGGER tc_dialog_event_immutable;
COMMIT;
