use sha2::{Digest, Sha256};
use std::{env, fs, path::PathBuf};

mod engine_config;

fn main() {
    println!("cargo:rerun-if-changed=engine_config.rs");
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("../bootstrap/build");
    let read = |name: &str| {
        let path = root.join(name);
        println!("cargo:rerun-if-changed={}", path.display());
        fs::read(&path).unwrap_or_else(|e| {
            panic!(
                "{}: {e}; run python bootstrap/bootstrap_compiler.py first",
                path.display()
            )
        })
    };
    let manifest: serde_json::Value =
        serde_json::from_slice(&read("compiler-manifest.json")).unwrap();
    assert_eq!(manifest["stage2_equals_stage3"], true);
    assert_eq!(manifest["host_passes_after_seed"], false);
    assert_eq!(manifest["host_fallback_guards"], true);
    let hash = |bytes: &[u8]| format!("{:x}", Sha256::digest(bytes));
    assert_eq!(manifest["source_sha256"], hash(&read("compiler.bundle.py")));
    let stages = manifest["stages"].as_array().unwrap();
    assert_eq!(stages.len(), 3);
    for (i, entry) in stages.iter().enumerate() {
        let name = format!("compiler.stage{}.wasm", i + 1);
        assert_eq!(entry["file"], name);
        assert_eq!(entry["sha256"], hash(&read(&name)));
    }
    let compiler = read("compiler.stage3.wasm");
    assert_eq!(read("compiler.stage2.wasm"), compiler);
    assert_eq!(read("compiler.wasm"), compiler);
    assert!(compiler.starts_with(b"\0asm\x01\0\0\0"));
    // Explicit TARGET disables host CPU-feature inference. Never accidentally
    // embed code specialized for the build machine (or for Cargo's HOST).
    let mut config = engine_config::config();
    config.target(&env::var("TARGET").unwrap()).unwrap();
    let engine = wasmtime::Engine::new(&config).unwrap();
    let compiled = engine.precompile_module(&compiler).unwrap();
    fs::write(
        PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("compiler.cwasm"),
        compiled,
    )
    .unwrap();
}
