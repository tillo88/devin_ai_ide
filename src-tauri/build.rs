fn main() {
    let attributes =
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "connect_frontdoor",
            "desktop_config_status",
            "test_frontdoor_connection",
            "save_frontdoor_config",
            "select_and_sync_local_workspace",
            "sync_local_workspace",
            "apply_local_workspace_changes",
        ]));
    tauri_build::try_build(attributes).expect("failed to build DEVIN desktop manifest")
}
