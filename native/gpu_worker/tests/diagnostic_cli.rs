use std::process::Command;

#[test]
fn diagnostic_cli_json_and_exit_code_match_without_requiring_a_gpu() {
    let output = Command::new(env!("CARGO_BIN_EXE_PuriPulyHeartGpuWorker"))
        .arg("--diagnose-vulkan")
        .output()
        .unwrap();
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["schema_version"], 1);
    assert!(report["vulkan_loader"].is_object());
    assert!(report["backend_initialized"].is_boolean());
    assert!(report["backend_available"].is_boolean());
    assert!(report["all_devices"].is_array());
    assert!(report["vulkan_devices"].is_array());
    assert_eq!(
        output.status.code(),
        Some(if report["error"].is_null() { 0 } else { 3 })
    );
}
