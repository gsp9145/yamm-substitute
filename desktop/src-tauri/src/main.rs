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

struct Backend(Mutex<Option<Child>>);

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
        .setup(|app| {
            let port = free_port();
            let exe = backend_exe(&app.handle());

            let mut cmd = Command::new(&exe);
            cmd.env("CREATORCRM_DESKTOP", "1")
                .env("CREATORCRM_PORT", port.to_string());
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
