// CreatorCRM desktop shell — spawns the bundled Python backend as a sidecar,
// waits for it to come up, then opens the app window pointing at it.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_updater::UpdaterExt;

struct Backend(Mutex<Option<Child>>);

fn data_file(name: &str) -> Option<std::path::PathBuf> {
    std::env::var("HOME")
        .ok()
        .map(|h| std::path::PathBuf::from(format!("{h}/Library/Application Support/CreatorCRM/{name}")))
}

/// Append a line to the updater log in the app data dir (for support/debugging).
fn ulog(msg: &str) {
    if let Some(path) = data_file("updater.log") {
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            let _ = writeln!(f, "{msg}");
        }
    }
}

/// Write the current update status to a file the Flask UI polls (/api/update-status).
/// The page reads this to show a "downloading…/ready" banner — the webview can't call
/// the Tauri API directly because the UI is loaded from an external (localhost) URL.
fn write_status(json: &str) {
    if let Some(path) = data_file("update_status.json") {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(path, json);
    }
}

/// Best-effort background update: check the manifest, and if a newer signed
/// build exists, download + install it. Applies on next launch. Progress is
/// written to update_status.json (for the UI); errors go to updater.log.
async fn check_for_updates(app: tauri::AppHandle) {
    ulog("[updater] check starting");
    write_status(r#"{"state":"checking"}"#);
    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            ulog(&format!("[updater] updater() error: {e}"));
            write_status(r#"{"state":"idle"}"#);
            return;
        }
    };
    match updater.check().await {
        Ok(Some(update)) => {
            let v = update.version.clone();
            ulog(&format!("[updater] update available -> {v}"));
            write_status(&format!(r#"{{"state":"downloading","version":"{v}","progress":0}}"#));
            let v_dl = v.clone();
            let mut downloaded: u64 = 0;
            let mut last_pct: i64 = -5;
            let res = update
                .download_and_install(
                    move |chunk: usize, total: Option<u64>| {
                        downloaded += chunk as u64;
                        if let Some(t) = total {
                            if t > 0 {
                                let pct = (downloaded as f64 / t as f64 * 100.0) as i64;
                                if pct >= last_pct + 3 {
                                    last_pct = pct;
                                    write_status(&format!(
                                        r#"{{"state":"downloading","version":"{v_dl}","progress":{pct}}}"#
                                    ));
                                }
                            }
                        }
                    },
                    || {},
                )
                .await;
            match res {
                Ok(_) => {
                    ulog("[updater] installed OK (applies next launch)");
                    write_status(&format!(r#"{{"state":"ready","version":"{v}"}}"#));
                }
                Err(e) => {
                    ulog(&format!("[updater] install error: {e}"));
                    write_status(r#"{"state":"error"}"#);
                }
            }
        }
        Ok(None) => {
            ulog("[updater] no update available (current is latest)");
            write_status(r#"{"state":"current"}"#);
        }
        Err(e) => {
            ulog(&format!("[updater] check error: {e}"));
            write_status(r#"{"state":"idle"}"#);
        }
    }
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("no free port")
        .local_addr()
        .unwrap()
        .port()
}

fn health_ok(port: u16) -> bool {
    if let Ok(mut s) = TcpStream::connect(("127.0.0.1", port)) {
        let _ = s.set_read_timeout(Some(Duration::from_millis(800)));
        let req = format!(
            "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        );
        if s.write_all(req.as_bytes()).is_ok() {
            let mut buf = String::new();
            let _ = s.read_to_string(&mut buf);
            return buf.starts_with("HTTP/1.1 200") || buf.starts_with("HTTP/1.0 200");
        }
    }
    false
}

fn backend_exe(app: &tauri::AppHandle) -> PathBuf {
    // Bundled app: Resources/backend/creatorcrm-backend (onedir layout)
    if let Ok(res) = app.path().resource_dir() {
        let bundled = res.join("backend").join("creatorcrm-backend");
        if bundled.exists() {
            return bundled;
        }
    }
    // Dev fallback: PyInstaller output next to the crate
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../backend-dist/creatorcrm-backend/creatorcrm-backend")
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let port = free_port();
            let exe = backend_exe(&app.handle());

            let mut cmd = Command::new(&exe);
            cmd.env("CREATORCRM_DESKTOP", "1")
                .env("CREATORCRM_PORT", port.to_string())
                .env("CREATORCRM_VERSION", env!("CARGO_PKG_VERSION"));
            // Build-time product config (OAuth client, relay URL) passes through
            for key in [
                "CREATORCRM_OAUTH_CLIENT_ID",
                "CREATORCRM_OAUTH_CLIENT_SECRET",
                "CREATORCRM_RELAY_URL",
            ] {
                if let Ok(v) = std::env::var(key) {
                    cmd.env(key, v);
                }
            }
            let child = cmd.spawn().map_err(|e| {
                format!("failed to start backend at {}: {e}", exe.display())
            })?;
            app.manage(Backend(Mutex::new(Some(child))));

            // Wait for readiness (PyInstaller onedir boots in ~1-2s; allow 30s)
            for _ in 0..120 {
                if health_ok(port) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(250));
            }

            let url = format!("http://127.0.0.1:{port}").parse().unwrap();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("CreatorCRM")
                .inner_size(1320.0, 860.0)
                .min_inner_size(900.0, 600.0)
                .build()?;

            // Check for updates in the background (non-blocking).
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                check_for_updates(handle).await;
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error building CreatorCRM")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Backend>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}
