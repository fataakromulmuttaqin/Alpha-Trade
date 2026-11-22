//! Notifikasi Telegram (opsional): env AT_TG_TOKEN + AT_TG_CHAT_ID.

use serde_json::json;

#[derive(Clone)]
pub struct Telegram {
    pub token: String,
    pub chat_id: String,
}

impl Telegram {
    pub fn from_parts(token: &str, chat_id: &str) -> Option<Telegram> {
        if token.trim().is_empty() || chat_id.trim().is_empty() { return None; }
        Some(Telegram { token: token.trim().into(), chat_id: chat_id.trim().into() })
    }

    /// Uji kirim — kembalikan pesan error bila gagal (untuk tombol TES di UI).
    pub async fn test_send(client: &reqwest::Client, token: &str, chat_id: &str) -> Result<(), String> {
        let url = format!("https://api.telegram.org/bot{}/sendMessage", token.trim());
        let resp = client.post(&url)
            .json(&json!({"chat_id": chat_id.trim(), "text": "✅ Alpha-Trade: tes notifikasi berhasil"}))
            .timeout(std::time::Duration::from_secs(12))
            .send().await.map_err(|e| format!("jaringan: {e}"))?;
        let status = resp.status();
        let body: serde_json::Value = resp.json().await.unwrap_or_default();
        if status.is_success() && body["ok"].as_bool().unwrap_or(false) { Ok(()) }
        else {
            let desc = body["description"].as_str().unwrap_or("respons tidak dikenal");
            Err(format!("Telegram: {status} — {desc}"))
        }
    }

    /// Kirim fire-and-forget (error diabaikan — notifikasi bukan jalur trading).
    pub async fn send(&self, client: &reqwest::Client, text: &str) {
        let url = format!("https://api.telegram.org/bot{}/sendMessage", self.token);
        let _ = client.post(&url)
            .json(&json!({"chat_id": self.chat_id, "text": text}))
            .timeout(std::time::Duration::from_secs(10))
            .send().await;
    }
}
