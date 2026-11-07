# Object access design

The preview page and source storage are separate boundaries. Public logo and preview assets are delivered from static-delivery. The internal application reads shared-doc-store after authentication. Giving allUsers objectViewer on a source resource permits direct object reads that bypass the preview page. The authorization implementation supports exact principals and allUsers, without inherited groups or provider-specific deny policies.
