# Repair review criteria

A repair must enforce private source storage access, preserve authenticated application and backup-worker reads, preserve public asset delivery, and preserve staging-worker reads. Changing only page visibility cannot revoke a direct storage grant. Declared checks are compiled from access-requirements before the proposed change is applied. No actual GCP resources are created or modified.
