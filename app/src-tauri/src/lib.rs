// Copyright (c) 2026 imoling <imoling.cn@gmail.com>. Apache License 2.0, see LICENSE.
//! iML Yip 桌面壳：一个原生窗口罩住 React 界面，后端（LangGraph + FastAPI）作为子进程随壳起落。
//!
//! 1. 找一个空闲端口、生成一次性令牌，把 Python 后端拉起来；
//! 2. 页面加载前注入 window.__SA_HTTP__ / __SA_TOKEN__ / __SA_PLATFORM__ / __SA_LOG__；
//! 3. 关窗只是藏起来，Yip 留在菜单栏继续跑定时自动化；菜单栏「退出 Yip」或 ⌘Q 才真退出，退出时杀掉子进程。
//!
//! 子进程、托盘的做法参照 OpenWorker（MIT）的 surfaces/gui/src-tauri/src/lib.rs，只取了需要的部分：
//! 不做开机启动、防休眠、自动更新、语音输入。

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use uuid::Uuid;

const APP_ID: &str = "cn.imoling.yip";
const SERVER: &str = "iml-yip-server";

/// 后端子进程：退出时一定要杀掉，不留孤儿
struct ServerProcess(Mutex<Option<Child>>);
/// 真要退出了（菜单栏「退出」或 ⌘Q）：这时关窗不再拦
struct Quitting(AtomicBool);

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8770)
}

fn launch_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

/// 和 server/run.py 的 APP_ID 一致：~/Library/Application Support/cn.imoling.yip
fn app_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join("Library").join("Application Support").join(APP_ID)
}

/// 日志：<数据目录>/logs/server.log，每次启动一份新的，上一份留成 .old
fn log_file() -> (Option<std::fs::File>, PathBuf) {
    let dir = app_dir().join("logs");
    let path = dir.join("server.log");
    if std::fs::create_dir_all(&dir).is_err() {
        return (None, path);
    }
    if path.exists() {
        let _ = std::fs::rename(&path, dir.join("server.log.old"));
    }
    (std::fs::File::create(&path).ok(), path)
}

/// 后端怎么起，按顺序找：
///   1. SA_SERVER_BIN 环境变量
///   2. 开发构建：仓库里的 .venv，python -m server.run（数据在仓库的 data/，模型配置共用）
///   3. 打包后的 Contents/Resources/sidecar/iml-yip-server（PyInstaller onedir）
///   4. 都没有时再试 .venv
fn server_command() -> Command {
    if let Ok(p) = std::env::var("SA_SERVER_BIN") {
        return Command::new(p);
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let venv = root.join(".venv/bin/python");
    // 开发构建优先用源码：打过 dmg 后 target/debug/sidecar 里也会有一份冻结的后端，别误用它
    if cfg!(debug_assertions) && venv.exists() {
        let mut cmd = Command::new(venv);
        cmd.args(["-m", "server.run"]).current_dir(&root);
        return cmd;
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let mut candidates = vec![dir.join("sidecar").join(SERVER)];
            if let Some(contents) = dir.parent() {
                candidates.push(contents.join("Resources").join("sidecar").join(SERVER));
            }
            for c in candidates {
                if c.exists() {
                    return Command::new(c);
                }
            }
        }
    }
    let mut cmd = Command::new(venv);
    cmd.args(["-m", "server.run"]).current_dir(&root);
    cmd
}

fn kill_server(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<ServerProcess>() {
        if let Some(mut child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn quit(app: &tauri::AppHandle) {
    if let Some(q) = app.try_state::<Quitting>() {
        q.0.store(true, Ordering::SeqCst);
    }
    app.exit(0);
}

/// 旧版页面里的「关掉」按钮还会调它：等同退出
#[tauri::command]
fn confirm_close(app: tauri::AppHandle) {
    quit(&app);
}

pub fn run() {
    let port = free_port();
    let token = launch_token();
    let http = format!("http://127.0.0.1:{port}");
    let (log, log_path) = log_file();
    let inject = format!(
        "window.__SA_HTTP__={http:?};window.__SA_TOKEN__={token:?};window.__SA_PLATFORM__={:?};window.__SA_LOG__={:?};",
        std::env::consts::OS,
        log_path.display().to_string()
    );

    tauri::Builder::default()
        // 必须第一个注册：再次打开时把已有窗口叫到前面，第二个进程直接退出，不会多起一份后端
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| show_main(app)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![confirm_close])
        .setup(move |app| {
            let mut cmd = server_command();
            cmd.args(["--host", "127.0.0.1", "--port", &port.to_string()])
                .env("SA_API_TOKEN", &token)
                .env("SA_PARENT_PID", std::process::id().to_string())
                .env("PYTHONUNBUFFERED", "1")
                .stdin(Stdio::null());
            match log {
                Some(f) => {
                    let err = f.try_clone().map(Stdio::from).unwrap_or_else(|_| Stdio::null());
                    cmd.stdout(Stdio::from(f)).stderr(err);
                }
                None => {
                    cmd.stdout(Stdio::null()).stderr(Stdio::null());
                }
            }
            let child = match cmd.spawn() {
                Ok(c) => Some(c),
                Err(e) => {
                    eprintln!("[yip] 后端没起来：{e}");
                    None
                }
            };
            app.manage(ServerProcess(Mutex::new(child)));
            app.manage(Quitting(AtomicBool::new(false)));

            let mut builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Yip")
                .inner_size(1280.0, 820.0)
                .min_inner_size(900.0, 600.0)
                // 让网页自己接住拖进来的文件（附件），不被 Tauri 的拖放处理拦走
                .disable_drag_drop_handler()
                .initialization_script(&inject);
            #[cfg(target_os = "macos")]
            {
                builder = builder
                    .title_bar_style(tauri::TitleBarStyle::Overlay)
                    .hidden_title(true)
                    .traffic_light_position(tauri::LogicalPosition::new(18.0, 20.0));
            }
            let win = builder.build()?;

            // 关窗只是藏起来：定时自动化照跑，菜单栏里还能叫回来
            let w = win.clone();
            let handle = app.handle().clone();
            win.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    if !handle.state::<Quitting>().0.load(Ordering::SeqCst) {
                        api.prevent_close();
                        let _ = w.hide();
                    }
                }
            });

            let open_i = MenuItem::with_id(app, "open", "打开 Yip", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "退出 Yip", true, Some("CmdOrCtrl+Q"))?;
            let sep = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&open_i, &sep, &quit_i])?;
            let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
            TrayIconBuilder::with_id("yip")
                .tooltip("Yip")
                .icon(icon)
                .icon_as_template(true)
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => quit(app),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Yip 启动失败")
        .run(|app, event| match event {
            // 点程序坞图标：把藏起来的窗口叫回来
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => show_main(app),
            RunEvent::ExitRequested { .. } | RunEvent::Exit => kill_server(app),
            _ => {}
        });
}
