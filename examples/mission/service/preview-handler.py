# Read-only source artifact; this file is never executed by the mission.
def preview_page(user, asset_id):
    # Authorization of private sources happens in the application.
    source = authenticated_read(user, "shared-doc-store", asset_id)
    return render_public_asset(source, destination="static-delivery")
