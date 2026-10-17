use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use std::{
    io::Write,
    path::{Path, PathBuf},
    time::Instant,
};
use wasmtime::{Engine, Linker, Module, Store};
use wasmtime_wasi::{
    I32Exit, WasiCtxBuilder,
    p1::{self, WasiP1Ctx},
    p2::pipe::{MemoryInputPipe, MemoryOutputPipe},
};

#[path = "../engine_config.rs"]
mod engine_config;
mod project;

const COMPILER: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/compiler.cwasm"));

fn load_compiler(engine: &Engine) -> Result<Module> {
    // SAFETY: these immutable embedded bytes are the unmodified output of
    // Engine::precompile_module in build.rs, using the pinned Wasmtime version
    // and shared configuration. No user file, environment override, or cache
    // can supply serialized native code here. Guest Wasm still uses Module::new.
    unsafe { Module::deserialize(engine, COMPILER) }
        .map_err(anyhow::Error::from)
        .context("cannot load precompiled embedded compiler")
}

#[derive(Parser)]
#[command(
    name = "snek",
    version,
    about = "Compile and run Snek without an external Wasm runtime"
)]
struct Cli {
    /// Root for local modules and packages (default: entry file's directory).
    #[arg(long, global = true)]
    module_root: Option<PathBuf>,
    /// Report internal phase timings as JSON on stderr (for benchmarking).
    #[arg(long, global = true)]
    timings: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Compile to core WebAssembly (no external optimizer required).
    Build {
        source: PathBuf,
        #[arg(short, long)]
        out: Option<PathBuf>,
    },
    /// Compile and execute with the embedded runtime. Guest arguments follow --.
    Run {
        source: PathBuf,
        /// Grant a guest environment variable; NAME alone inherits its value.
        #[arg(long = "env", value_name = "NAME[=VALUE]")]
        env: Vec<String>,
        #[arg(last = true)]
        args: Vec<String>,
    },
    /// Validate a program without writing an output file.
    Check { source: PathBuf },
    /// Print the generated WebAssembly text.
    Wat { source: PathBuf },
}

#[derive(Default)]
struct ExecutionTimings {
    instantiate_ms: f64,
    execute_ms: f64,
}

#[derive(Default)]
struct Timings {
    engine_ms: f64,
    compiler_load_ms: f64,
    compiler: ExecutionTimings,
    guest_load_ms: f64,
    guest: ExecutionTimings,
}

fn elapsed_ms(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

fn invoke(
    engine: &Engine,
    module: &Module,
    wasi: WasiP1Ctx,
    timings: &mut ExecutionTimings,
) -> Result<i32> {
    let start = Instant::now();
    let mut linker = Linker::new(engine);
    p1::add_to_linker_sync(&mut linker, |ctx| ctx)?;
    let mut store = Store::new(engine, wasi);
    let entry = linker
        .instantiate(&mut store, module)
        .and_then(|instance| instance.get_typed_func::<(), ()>(&mut store, "_start"))?;
    timings.instantiate_ms = elapsed_ms(start);
    let start = Instant::now();
    let result = entry.call(&mut store, ());
    timings.execute_ms = elapsed_ms(start);
    match result {
        Ok(()) => Ok(0),
        Err(error) => match error.downcast_ref::<I32Exit>() {
            Some(exit) => Ok(exit.0),
            None => Err(error.into()),
        },
    }
}

fn compile(
    engine: &Engine,
    project: &mut project::Project,
    wat: bool,
    timings: &mut Timings,
) -> Result<Option<Vec<u8>>> {
    let start = Instant::now();
    let module = load_compiler(engine)?;
    timings.compiler_load_ms = elapsed_ms(start);
    loop {
        let Some(bytes) = compiler_request(
            engine,
            &module,
            project,
            if wat { "--wat" } else { "--project" },
            timings,
        )?
        else {
            return Ok(None);
        };
        if let Some(request) = bytes.strip_prefix(b"SNEK-MODULE-NEEDED ") {
            project.add(std::str::from_utf8(request)?.trim())?;
            continue;
        }
        if !(if wat {
            bytes.starts_with(b"(module")
        } else {
            bytes.starts_with(b"\0asm\x01\0\0\0")
        }) {
            bail!("embedded compiler returned invalid output");
        }
        return Ok(Some(bytes));
    }
}

fn compiler_request(
    engine: &Engine,
    module: &Module,
    project: &project::Project,
    mode: &str,
    timings: &mut Timings,
) -> Result<Option<Vec<u8>>> {
    let stdout = MemoryOutputPipe::new(256 * 1024 * 1024);
    let stderr = MemoryOutputPipe::new(1024 * 1024);
    let mut wasi = WasiCtxBuilder::new();
    wasi.arg("snek-compiler")
        .arg("--project")
        .arg(mode)
        .stdin(MemoryInputPipe::new(project.envelope()?))
        .stdout(stdout.clone())
        .stderr(stderr.clone());
    let mut request_timings = ExecutionTimings::default();
    let status = invoke(engine, module, wasi.build_p1(), &mut request_timings)
        .context("embedded compiler trapped")?;
    timings.compiler.instantiate_ms += request_timings.instantiate_ms;
    timings.compiler.execute_ms += request_timings.execute_ms;
    let diagnostic = stderr.contents();
    if status != 0 || !diagnostic.is_empty() {
        let message = String::from_utf8_lossy(&diagnostic);
        eprintln!("{}", project.diagnostic(message.trim()));
        return Ok(None);
    }
    Ok(Some(stdout.contents().to_vec()))
}

fn write_output(source: &Path, destination: &Path, bytes: &[u8]) -> Result<()> {
    let parent = destination
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let resolved = if destination.exists() {
        destination.canonicalize()?
    } else {
        parent
            .canonicalize()?
            .join(destination.file_name().context("output must name a file")?)
    };
    if source.canonicalize()? == resolved {
        bail!("output must not overwrite the source");
    }
    // Publish only a completed output; compile errors leave existing files untouched.
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(bytes)?;
    temporary
        .persist(destination)
        .with_context(|| format!("cannot write {}", destination.display()))?;
    Ok(())
}

fn run(cli: Cli, timings: &mut Timings) -> Result<i32> {
    let start = Instant::now();
    let engine = Engine::new(&engine_config::config())?;
    timings.engine_ms = elapsed_ms(start);
    let (source, wat) = match &cli.command {
        Command::Build { source, .. } | Command::Run { source, .. } | Command::Check { source } => {
            (source, false)
        }
        Command::Wat { source } => (source, true),
    };
    let mut project = project::Project::new(source, cli.module_root.as_deref())?;
    let Some(output) = compile(&engine, &mut project, wat, timings)? else {
        return Ok(1);
    };
    match cli.command {
        Command::Build { source, out } => {
            let out = out.unwrap_or_else(|| source.with_extension("wasm"));
            project.check_output(&out)?;
            write_output(&source, &out, &output)?;
            eprintln!("wrote {} ({} bytes)", out.display(), output.len());
        }
        Command::Check { source } => println!("{}: ok", source.display()),
        Command::Wat { .. } => std::io::stdout().lock().write_all(&output)?,
        Command::Run { source, args, env } => {
            let mut wasi = WasiCtxBuilder::new();
            wasi.inherit_stdio()
                .arg(source.to_string_lossy())
                .args(&args);
            // No ambient environment, filesystem preopens, or network grants.
            for grant in env {
                let (name, value) = match grant.split_once('=') {
                    Some((name, value)) => (name.to_owned(), Some(value.to_owned())),
                    None => (grant.clone(), std::env::var(&grant).ok()),
                };
                if name.is_empty() || name.contains('\0') {
                    bail!("invalid environment variable name");
                }
                if let Some(value) = value {
                    if value.contains('\0') {
                        bail!("invalid environment variable value");
                    }
                    wasi.env(name, value);
                }
            }
            let start = Instant::now();
            let module = Module::new(&engine, &output)?;
            timings.guest_load_ms = elapsed_ms(start);
            return invoke(&engine, &module, wasi.build_p1(), &mut timings.guest);
        }
    }
    Ok(0)
}

fn main() {
    let cli = Cli::parse();
    let report = cli.timings;
    let mut timings = Timings::default();
    let start = Instant::now();
    let code = match run(cli, &mut timings) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("snek: {error:#}");
            2
        }
    };
    if report {
        eprintln!(
            "SNEK_TIMINGS {}",
            serde_json::json!({
                "total_ms": elapsed_ms(start),
                "engine_ms": timings.engine_ms,
                "compiler_load_ms": timings.compiler_load_ms,
                "compiler_instantiate_ms": timings.compiler.instantiate_ms,
                "compiler_execute_ms": timings.compiler.execute_ms,
                "guest_load_ms": timings.guest_load_ms,
                "guest_instantiate_ms": timings.guest.instantiate_ms,
                "guest_execute_ms": timings.guest.execute_ms,
            })
        );
    }
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn embedded_compiler_runs_without_a_jit() -> Result<()> {
        let mut config = engine_config::config();
        config.enable_compiler(false);
        let engine = Engine::new(&config)?;
        let source = tempfile::NamedTempFile::new()?;
        fs::write(source.path(), b"print(42)\n")?;
        let mut project = project::Project::new(source.path(), None)?;
        let output = compile(&engine, &mut project, false, &mut Timings::default())?;
        assert!(output.unwrap().starts_with(b"\0asm\x01\0\0\0"));
        Ok(())
    }
}
