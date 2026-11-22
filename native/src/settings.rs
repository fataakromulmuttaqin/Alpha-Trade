//! Pengaturan aplikasi — persisten di data/settings.json.
//! Notifikasi Telegram dapat diatur dari UI (halaman PENGATURAN).

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Settings {
    #[serde(default)]
    pub tg_token: String,
    #[serde(default)]
    pub tg_chat_id: String,
    #[serde(default = "default_tg_on")]
    pub tg_on: bool,
    #[serde(default)]
    pub notify_signals: bool,
    #[serde(default = "default_true")]
    pub notify_trades: bool,
}

fn default_tg_on() -> bool { true }
fn default_true() -> bool { true }

impl Default for Settings {
    fn default() -> Self {
        Settings {
            tg_token: String::new(),
            tg_chat_id: String::new(),
            tg_on: true,
            notify_signals: true,
            notify_trades: true,
        }
    }
}

impl Settings {
    pub fn load_or_default(path: &str) -> Settings {
        if let Ok(txt) = std::fs::read_to_string(path) {
            if let Ok(s) = serde_json::from_str::<Settings>(&txt) {
                return s;
            }
        }
        // fallback: env (migrasi dari konfigurasi lama)
        let mut s = Settings::default();
        if let Ok(t) = std::env::var("AT_TG_TOKEN") { s.tg_token = t; }
        if let Ok(c) = std::env::var("AT_TG_CHAT_ID") { s.tg_chat_id = c; }
        s
    }

    pub fn save(&self, path: &str) {
        if let Some(dir) = std::path::Path::new(path).parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(json) = serde_json::to_string_pretty(self) {
            let _ = std::fs::write(path, json);
        }
    }
}
