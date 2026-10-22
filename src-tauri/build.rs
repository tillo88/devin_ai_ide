fn main() {
    let attributes =
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "connect_frontdoor",
            "desktop_config_status",
            "test_frontdoor_connection",
            "save_frontdoor_config",
            "select_and_sync_local_workspace",
            "sync_local_workspace",
            "local_workspace_tree",
            "local_workspace_read",
            "local_workspace_search",
            "local_workspace_context",
            "local_workspace_evidence_v2",
            "run_local_workspace_command",
            "cancel_local_workspace_command",
            "apply_local_workspace_plan",
            "apply_local_workspace_changes",
            "local_workspace_changes",
            "undo_local_workspace_plan",
            "start_local_program",
            "act_local_program",
            "stop_local_program",
            "desktop_http_stream",
            "desktop_http_cancel",
            "desktop_update_check",
            "desktop_update_open",
        ]));
    tauri_build::try_build(attributes).expect("failed to build DEVIN desktop manifest")
}
