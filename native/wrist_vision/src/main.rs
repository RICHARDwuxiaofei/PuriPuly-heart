#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]
#[cfg(windows)]
use puripuly_heart_wrist_vision::platform::{render, vr::Vr};
use puripuly_heart_wrist_vision::state::{ChatRow, InputGate, Mode};
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, Read, Write},
    sync::mpsc,
    time::{Duration, Instant},
};

fn emit(value: Value) {
    let mut out = io::stdout().lock();
    let _ = serde_json::to_writer(&mut out, &value);
    let _ = out.write_all(b"\n");
    let _ = out.flush();
}
fn failure(code: &str) {
    emit(json!({"type":"error","code":code}));
}

#[cfg(windows)]
fn run() -> Result<(), &'static str> {
    let mock = std::env::args().any(|s| s == "--mock");
    let mut vr = if mock { None } else { Some(Vr::new()?) };
    let (tx, rx) = mpsc::sync_channel(8);
    std::thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = stdin.lock();
        loop {
            let mut line = Vec::new();
            let read = reader.by_ref().take(512_001).read_until(b'\n', &mut line);
            match read {
                Ok(0) | Err(_) => break,
                _ => {}
            }
            if line.len() > 512_000 {
                break;
            }
            if let Ok(value) = serde_json::from_slice::<Value>(&line) {
                if tx.send(value).is_err() {
                    return;
                }
            }
        }
        let _ = tx.send(json!({"type":"exit"}));
    });
    emit(json!({"type":"ready","protocol":1,"mock":mock}));
    let mut mode = Mode::Chat;
    let mut rows = Vec::<ChatRow>::new();
    let mut result = Vec::<ChatRow>::new();
    let mut page = 0;
    let mut title = "PuriPuly Wrist Vision".to_owned();
    let mut status = String::new();
    let mut labels = vec![
        "Screenshot".into(),
        "Older".into(),
        "Newer".into(),
        "Close".into(),
    ];
    let mut gate = InputGate::default();
    let mut point = None;
    let mut dirty = true;
    let mut last_render = Instant::now();
    let mut framing_since = Instant::now();
    let mut last_error = String::new();
    let mut capture_id = 0_u64;
    let mut last_config = Value::Null;
    loop {
        while let Ok(value) = rx.try_recv() {
            match value["type"].as_str().unwrap_or("") {
                "exit" => return Ok(()),
                "config" => {
                    if last_config != Value::Null
                        && last_config != value["values"]
                        && matches!(mode, Mode::Framing | Mode::Busy)
                    {
                        emit(json!({"type":"cancel","id":capture_id}));
                        capture_id += 1;
                        mode = Mode::Chat;
                        gate.reset();
                        if let Some(v) = &vr {
                            v.hide_all();
                        }
                    }
                    last_config = value["values"].clone();
                    if let Some(v) = vr.as_mut() {
                        v.configure(&value["values"])?;
                    }
                    if let Some(copy) = value["labels"].as_array() {
                        labels = copy
                            .iter()
                            .take(4)
                            .map(|s| s.as_str().unwrap_or("").to_owned())
                            .collect();
                    }
                    title = value["title"]
                        .as_str()
                        .unwrap_or("PuriPuly Wrist Vision")
                        .into();
                    dirty = true;
                }
                "snapshot" => {
                    rows = serde_json::from_value(value["rows"].clone()).unwrap_or_default();
                    rows.truncate(100);
                    if mode == Mode::Chat {
                        page = 0;
                    }
                    dirty = true;
                }
                "status" => {
                    status = value["text"]
                        .as_str()
                        .unwrap_or("")
                        .chars()
                        .take(600)
                        .collect();
                    dirty = true;
                }
                "result" if value["id"].as_u64() == Some(capture_id) => {
                    result = serde_json::from_value(value["rows"].clone()).unwrap_or_default();
                    result.truncate(128);
                    mode = Mode::Result;
                    page = 0;
                    dirty = true;
                }
                "cancel" => {
                    mode = Mode::Chat;
                    capture_id += 1;
                    if let Some(v) = &vr {
                        v.hide_all();
                    }
                    gate.reset();
                    dirty = true;
                }
                "mock_render" if mock => {
                    let pixels = render::panel(&title, &status, &rows, &labels, page, None)?;
                    let checksum = pixels.iter().fold(0_u64, |a, v| a.wrapping_add(*v as u64));
                    let mut result = json!({"type":"mock_rendered","size":[render::WIDTH,render::HEIGHT],"checksum":checksum});
                    if value["dump_rgba"].as_bool() == Some(true) {
                        result["rgba_hex"] = json!(pixels
                            .iter()
                            .map(|v| format!("{v:02x}"))
                            .collect::<String>());
                    }
                    emit(result);
                }
                "mock_confirm" if mock => {
                    capture_id += 1;
                    emit(json!({"type":"framing"}));
                    emit(json!({"type":"capturing","id":capture_id}));
                    let timestamp = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map_err(|_| "clock_unavailable")?
                        .as_millis();
                    emit(
                        json!({"type":"capture","id":capture_id,"size":[32,16],"rgb_hex":"dddddd".repeat(512),"metadata":{"captured_at_ms":timestamp,"scene_pid":1,"mock":true}}),
                    );
                    mode = Mode::Busy;
                }
                _ => {}
            }
        }
        if let Some(v) = vr.as_mut() {
            let input = v.place().and_then(|_| v.input());
            match input {
                Ok((trigger, cancel, hit)) => {
                    last_error.clear();
                    if point != hit {
                        point = hit;
                        if last_render.elapsed() > Duration::from_millis(80) {
                            dirty = true;
                        }
                    }
                    if cancel && mode != Mode::Chat {
                        emit(json!({"type":"cancel","id":capture_id}));
                        mode = Mode::Chat;
                        capture_id += 1;
                        v.hide_all();
                        gate.reset();
                        dirty = true;
                    } else if gate.edge(trigger) {
                        if mode == Mode::Framing {
                            capture_id += 1;
                            emit(json!({"type":"capturing","id":capture_id}));
                            match v.capture() {
                                Ok(mut capture) => {
                                    capture["id"] = json!(capture_id);
                                    emit(capture);
                                    mode = Mode::Busy;
                                }
                                Err(code) => {
                                    failure(code);
                                    mode = Mode::Chat;
                                }
                            }
                            gate.reset();
                            dirty = true;
                        } else if let Some([x, y]) = hit {
                            if y >= 880. / render::HEIGHT as f32 {
                                match ((x * render::WIDTH as f32 - 16.) / 188.).floor() as i32 {
                                    0 if mode != Mode::Busy => {
                                        mode = Mode::Framing;
                                        framing_since = Instant::now();
                                        v.show_frame()?;
                                        gate.reset();
                                        emit(json!({"type":"framing"}));
                                    }
                                    0 => {}
                                    1 => {
                                        let count = if mode == Mode::Result {
                                            result.len()
                                        } else {
                                            rows.len()
                                        };
                                        page = (page + 1).min(count.saturating_sub(1) / 3);
                                        dirty = true;
                                    }
                                    2 => {
                                        page = page.saturating_sub(1);
                                        dirty = true;
                                    }
                                    3 if mode == Mode::Chat => {
                                        emit(json!({"type":"closed"}));
                                        return Ok(());
                                    }
                                    3 => {
                                        emit(json!({"type":"cancel","id":capture_id}));
                                        capture_id += 1;
                                        mode = Mode::Chat;
                                        v.hide_all();
                                        gate.reset();
                                        dirty = true;
                                    }
                                    _ => {}
                                }
                            }
                        }
                    }
                }
                Err(code) => {
                    v.hide_all();
                    gate.reset();
                    if mode == Mode::Busy || mode == Mode::Framing {
                        emit(json!({"type":"cancel","id":capture_id}));
                        capture_id += 1;
                        mode = Mode::Chat;
                    }
                    if last_error != code {
                        failure(code);
                        last_error = code.into();
                    }
                    dirty = true;
                }
            }
            if mode == Mode::Framing && framing_since.elapsed() > Duration::from_secs(45) {
                mode = Mode::Chat;
                v.hide_all();
                gate.reset();
                failure("framing_timeout");
                dirty = true;
            }
            if dirty && mode != Mode::Framing && last_error.is_empty() {
                let active = if mode == Mode::Result { &result } else { &rows };
                let mut pixels = render::panel(&title, &status, active, &labels, page, point)?;
                v.hide_frame();
                v.submit(&mut pixels)?;
                last_render = Instant::now();
                dirty = false;
            }
        }
        std::thread::sleep(Duration::from_millis(16));
    }
}

fn main() {
    #[cfg(windows)]
    if let Err(code) = run() {
        failure(code);
        std::process::exit(1);
    }
    #[cfg(not(windows))]
    {
        failure("windows_required");
        std::process::exit(1);
    }
}
