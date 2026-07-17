use std::env;
use std::path::PathBuf;

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let sdk = PathBuf::from(env::var_os("VULKAN_SDK").expect("VULKAN_SDK is required"));
    let source = sdk.join("Lib").join("vulkan-1.lib");
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR is required"));
    let alias = output.join("vulkan.lib");
    std::fs::copy(&source, &alias).expect("failed to stage Vulkan import library alias");
    println!("cargo:rustc-link-search=native={}", output.display());
    println!("cargo:rerun-if-env-changed=VULKAN_SDK");
    println!("cargo:rerun-if-changed={}", source.display());
}
