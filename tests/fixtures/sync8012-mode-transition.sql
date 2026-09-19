CREATE TRIGGER IF NOT EXISTS sync_projects_mode_epoch_transition
            BEFORE UPDATE OF project_sync_mode, migration_epoch ON sync_projects
            WHEN NOT (
                (
                    NEW.project_sync_mode = OLD.project_sync_mode
                    AND NEW.migration_epoch = OLD.migration_epoch
                )
                OR
                (
                    OLD.project_sync_mode = 'LEGACY'
                    AND OLD.migration_epoch = 0
                    AND NEW.project_sync_mode = 'MIGRATING'
                    AND NEW.migration_epoch = 1
                )
                OR
                (
                    OLD.project_sync_mode = 'MIGRATING'
                    AND OLD.migration_epoch >= 1
                    AND NEW.project_sync_mode = 'ID_BASED'
                    AND NEW.migration_epoch = OLD.migration_epoch
                )
            )
            BEGIN
                SELECT RAISE(ABORT, 'INVALID_PROJECT_MODE_TRANSITION');
            END;