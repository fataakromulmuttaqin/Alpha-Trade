//! Notifikasi Telegram (opsional): env AT_TG_TOKEN + AT_TG_CHAT_ID.

use serde_json::json;

#[derive(Clone)]
pub struct Telegram {
    pub token: String,
    pub chat_id: String,
}

impl Telegram {
    pub fn from_env() -> Option<Telegram> {
        let token = std::env::var("AT_TG_TOKEN").ok()?;
        let chat_id = std::env::var("AT_TG_CHAT_ID").ok()?;
        if token.trim().is_empty() || chat_id.trim().is_empty() { return None; }
        Some(Telegram { token: token.trim().into(), chat_id: chat_id.trim().into() })
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
