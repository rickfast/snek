/// Keep Wasm semantics identical for build-time precompilation and execution.
pub fn config() -> wasmtime::Config {
    let mut config = wasmtime::Config::new();
    config
        .wasm_gc(true)
        .wasm_function_references(true)
        .wasm_exceptions(true);
    config
}
