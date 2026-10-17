//! Filesystem boundary only. The embedded Snek compiler requests and links modules.
use anyhow::{Context, Result, bail};
use std::{
    fs,
    path::{Path, PathBuf},
};

pub struct Source {
    pub name: String,
    pub path: PathBuf,
    pub text: Option<String>,
    package: bool,
}

pub struct Project {
    root: PathBuf,
    entry: String,
    pub sources: Vec<Source>,
}

fn identifier(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c == '_' || c.is_alphabetic())
        && chars.all(|c| c == '_' || c.is_alphanumeric())
}

impl Project {
    pub fn new(path: &Path, root: Option<&Path>) -> Result<Self> {
        let path = path
            .canonicalize()
            .with_context(|| format!("cannot read {}", path.display()))?;
        let root = root.unwrap_or(path.parent().unwrap()).canonicalize()?;
        let relative = path
            .strip_prefix(&root)
            .context("entry source must be inside --module-root")?;
        let mut parts: Vec<String> = relative
            .with_extension("")
            .components()
            .map(|p| p.as_os_str().to_string_lossy().into_owned())
            .collect();
        let package = parts.last().is_some_and(|p| p == "__init__");
        if package {
            parts.pop();
            if parts.is_empty() {
                bail!(
                    "a package initializer entry requires --module-root above its package directory"
                );
            }
        }
        if let Some(last) = parts.last_mut() {
            if !identifier(last) {
                *last = "__entry__".into();
            }
        }
        if parts.is_empty() || !parts.iter().all(|p| identifier(p)) {
            bail!("project source paths must use Python identifiers");
        }
        let entry = parts.join(".");
        let text = fs::read_to_string(&path)
            .context("source must be UTF-8")?
            .replace("\r\n", "\n")
            .replace('\r', "\n");
        Ok(Self {
            root,
            entry: entry.clone(),
            sources: vec![Source {
                name: entry,
                path,
                text: Some(text),
                package,
            }],
        })
    }

    pub fn add(&mut self, name: &str) -> Result<()> {
        if self.sources.iter().any(|s| s.name == name) {
            bail!("module resolver made no progress");
        }
        if name.is_empty() || !name.split('.').all(identifier) {
            bail!("invalid module request: {name:?}");
        }
        let base = name
            .split('.')
            .fold(self.root.clone(), |path, part| path.join(part));
        let candidates = [
            PathBuf::from(format!("{}.snek", base.display())),
            PathBuf::from(format!("{}.py", base.display())),
            base.join("__init__.snek"),
            base.join("__init__.py"),
        ];
        let found: Vec<_> = candidates.iter().filter(|p| p.is_file()).collect();
        if found.len() > 1 {
            bail!("ambiguous module '{name}': {found:?}");
        }
        let (path, text, package) = if let Some(path) = found.first() {
            let package = path.file_stem().is_some_and(|s| s == "__init__");
            let path = path.canonicalize()?;
            if !path.starts_with(&self.root) {
                bail!("module '{name}' resolves outside --module-root");
            }
            if self.sources.iter().any(|s| s.path == path) {
                bail!("module '{name}' aliases another source file");
            }
            let text = fs::read_to_string(&path)
                .with_context(|| format!("cannot read UTF-8 module {}", path.display()))?
                .replace("\r\n", "\n")
                .replace('\r', "\n");
            (path, Some(text), package)
        } else {
            (base, None, false)
        };
        self.sources.push(Source {
            name: name.into(),
            path,
            text,
            package,
        });
        Ok(())
    }

    pub fn envelope(&self) -> Result<Vec<u8>> {
        let mut output = format!("SNEK-PROJECT-1\n{}\n{}\n", self.entry, self.sources.len());
        for source in &self.sources {
            let path = source
                .path
                .to_str()
                .context("project paths must be UTF-8")?;
            if path.contains(['\n', '\r']) {
                bail!("project paths cannot contain newlines");
            }
            let length = source
                .text
                .as_ref()
                .map_or(-1, |text| text.chars().count() as i64);
            output.push_str(&format!(
                "{}\n{}\n{}\n{length}\n",
                source.name,
                path,
                i32::from(source.package)
            ));
            if let Some(text) = &source.text {
                output.push_str(text);
                output.push('\n');
            }
        }
        Ok(output.into_bytes())
    }

    pub fn diagnostic(&self, diagnostic: &str) -> String {
        if let Some(rest) = diagnostic.strip_prefix("ValueError: line ") {
            if let Some((line, rest)) = rest.split_once(", column ") {
                if let Ok(mut line) = line.parse::<usize>() {
                    for source in &self.sources {
                        let length = source.text.as_deref().unwrap_or("").matches('\n').count() + 2;
                        if line <= length {
                            return format!(
                                "{}: line {line}, column {rest}",
                                source.path.display()
                            )
                            .replace("@user:", "");
                        }
                        line -= length;
                    }
                }
            }
        }
        diagnostic.to_owned()
    }

    pub fn check_output(&self, destination: &Path) -> Result<()> {
        let resolved = if destination.exists() {
            destination.canonicalize()?
        } else {
            destination
                .parent()
                .filter(|p| !p.as_os_str().is_empty())
                .unwrap_or(Path::new("."))
                .canonicalize()?
                .join(destination.file_name().context("output must name a file")?)
        };
        if self
            .sources
            .iter()
            .any(|s| s.text.is_some() && s.path == resolved)
        {
            bail!("output must not overwrite any project source");
        }
        Ok(())
    }
}
