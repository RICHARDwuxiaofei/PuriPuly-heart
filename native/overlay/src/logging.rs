use std::path::{Path, PathBuf};

use serde_json::Value;
use tokio::fs::{create_dir_all, File, OpenOptions};
use tokio::io::{self, AsyncWriteExt};
use tokio::sync::Mutex;

pub struct OverlayLogger {
    file: Mutex<File>,
    path: PathBuf,
}

impl OverlayLogger {
    pub async fn open(log_dir: impl AsRef<Path>) -> io::Result<Self> {
        create_dir_all(log_dir.as_ref()).await?;
        let path = log_dir.as_ref().join("puripuly_heart_overlay.log");
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .await?;
        Ok(Self {
            file: Mutex::new(file),
            path,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub async fn info(&self, message: impl AsRef<str>) -> io::Result<()> {
        self.log_line("INFO", message.as_ref()).await
    }

    pub async fn warn(&self, message: impl AsRef<str>) -> io::Result<()> {
        self.log_line("WARN", message.as_ref()).await
    }

    pub async fn error(&self, message: impl AsRef<str>) -> io::Result<()> {
        self.log_line("ERROR", message.as_ref()).await
    }

    pub async fn emit_stdout_event(&self, payload: &Value) -> io::Result<()> {
        self.write_stream_line(true, &format!("EVENT {}", payload)).await
    }

    pub async fn emit_stderr_event(&self, payload: &Value) -> io::Result<()> {
        self.write_stream_line(false, &format!("EVENT {}", payload)).await
    }

    async fn log_line(&self, level: &str, message: &str) -> io::Result<()> {
        let line = format!("[overlay][{level}] {message}\n");
        {
            let mut file = self.file.lock().await;
            file.write_all(line.as_bytes()).await?;
            file.flush().await?;
        }
        self.write_stream_line(level != "ERROR", line.trim_end()).await
    }

    async fn write_stream_line(&self, stdout: bool, line: &str) -> io::Result<()> {
        if stdout {
            let mut stream = tokio::io::stdout();
            stream.write_all(line.as_bytes()).await?;
            stream.write_all(b"\n").await?;
            stream.flush().await
        } else {
            let mut stream = tokio::io::stderr();
            stream.write_all(line.as_bytes()).await?;
            stream.write_all(b"\n").await?;
            stream.flush().await
        }
    }
}
